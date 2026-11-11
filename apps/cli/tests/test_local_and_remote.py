"""``--local`` is offline whatever the environment says, fails kindly without a database, and the
same command speaks to a real server over a real socket (SSE included)."""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mde_cli.client import Identity
from mde_cli.local import LocalApi
from mde_cli.main import app
from mde_context_api.composition import AppState
from mde_context_api.main import create_app
from mde_context_api.testing import TEST_TOKEN, serve_app

Script = Callable[..., str]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- --local


def test_local_is_mock_and_offline_even_when_the_environment_asks_for_live(
    runner: CliRunner, tmp_path: Path, write_script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    for name, value in (("LLM_MODE", "live"), ("OPENAI_API_KEY", key)):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MDE_FORBID_LIVE")
    result = runner.invoke(
        app,
        [
            "chat",
            "--local",
            "--memory",
            "--projects-dir",
            str(tmp_path / "p"),
            "--script",
            write_script(["hi"]),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "In-process API, mock mode (no key, no network)" in result.stdout
    assert key not in result.output  # no key was read, none printed

    # the in-process budget endpoint shows no live call was made
    async def live_calls() -> int:
        local = LocalApi(memory=True, projects_dir=tmp_path / "p2")
        client = await local.start(Identity("local", "tester"))
        try:
            return (await client.budget()).live_calls
        finally:
            await local.stop()

    assert asyncio.run(live_calls()) == 0


def test_local_without_a_database_is_one_line_and_never_shows_the_password(
    runner: CliRunner, write_script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"postgresql+psycopg://mde:pw-secret-123@127.0.0.1:{free_port()}/mde"
    monkeypatch.setenv("DATABASE_URL", url)
    result = runner.invoke(app, ["chat", "--local", "--script", write_script(["hi"])])
    assert result.exit_code == 1
    assert "error: Cannot reach the local PostgreSQL" in result.stderr
    assert "dev_db.py up" in result.stderr and "--memory" in result.stderr
    assert "pw-secret-123" not in result.output and "Traceback" not in result.output


def test_the_local_hand_off_goes_to_the_projects_folder_given(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    projects = tmp_path / "handoff"
    lines = ["An online shop for handmade candles", "finish", "1", "1"]
    result = runner.invoke(
        app,
        [
            "chat",
            "--local",
            "--memory",
            "--projects-dir",
            str(projects),
            "--tenant",
            "acme",
            "--script",
            write_script(lines),
        ],
    )
    assert result.exit_code == 0, result.output
    [spec] = list(projects.glob("acme/app_*/APP_SPEC.md"))
    assert re.search(r"^  repo: +" + re.escape(str(spec.parent)), result.stdout, re.M)


# --------------------------------------------------------------------------- a real server


@pytest.fixture
async def server(state: AppState):  # type: ignore[no-untyped-def]
    async with serve_app(create_app(state=state)) as base_url:
        yield base_url


async def test_a_whole_conversation_over_a_real_socket_follows_the_event_stream(
    server: str, write_script: Script, journey: list[str]
) -> None:
    script = write_script(journey)
    result = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        [
            "chat",
            "--url",
            server,
            "--token",
            TEST_TOKEN,
            "--tenant",
            "tenant-a",
            "--user",
            "user-1",
            "--script",
            script,
        ],
    )
    out = result.stdout
    assert result.exit_code == 0, result.output
    assert "In-process API" not in out  # this is the network client
    assert re.search(r"^  1\) .+$", out, re.M) and "=== Brief, version 1" in out
    assert re.search(r"^  \[build( \d+/\d+)?\] ", out, re.M)  # progress arrived over the stream
    assert "Spec ready." in out and "HANDED_OFF (spec_ready: 1, problems: 0)" in out


async def test_a_wrong_token_is_one_friendly_line_that_does_not_repeat_the_token(
    server: str, write_script: Script
) -> None:
    wrong = "not-the-right-token-123"
    result = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        ["chat", "--url", server, "--token", wrong, "--script", write_script(["hi"])],
    )
    assert result.exit_code == 1
    assert "did not accept the credentials" in result.stderr
    assert wrong not in result.output and "Traceback" not in result.output


async def test_resuming_a_session_by_id_over_the_network(server: str, write_script: Script) -> None:
    common = ["--url", server, "--token", TEST_TOKEN, "--tenant", "tenant-a", "--user", "user-1"]
    first = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        [
            "chat",
            *common,
            "--script",
            write_script(["An online shop for handmade candles"], "a.txt"),
        ],
    )
    sid = re.search(r"(ses_\w{26})", first.stdout)
    assert first.exit_code == 0 and sid is not None
    second = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        ["chat", *common, "--session", sid.group(1), "--script", write_script(["r*"], "b.txt")],
    )
    assert second.exit_code == 0, second.output
    assert f"Resumed session {sid.group(1)} (guided, DISCOVERING)." in second.stdout
    assert "Question 1 of" in second.stdout  # the pending card is shown again
    assert "Assistant: Hi!" not in second.stdout  # the past is not replayed


async def test_doctor_can_check_a_running_api(server: str) -> None:
    result = await asyncio.to_thread(CliRunner().invoke, app, ["doctor", "--url", server])
    assert f"api: ready at {server}" in result.output


async def test_a_stranger_cannot_resume_somebody_elses_session(
    server: str, write_script: Script
) -> None:
    owner = ["--url", server, "--token", TEST_TOKEN, "--tenant", "tenant-a", "--user", "user-1"]
    first = await asyncio.to_thread(
        CliRunner().invoke, app, ["chat", *owner, "--script", write_script(["hi"], "a.txt")]
    )
    sid = re.search(r"(ses_\w{26})", first.stdout)
    assert sid is not None
    stranger = [
        "--url",
        server,
        "--token",
        TEST_TOKEN,
        "--tenant",
        "tenant-a",
        "--user",
        "someone-else",
    ]
    result = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        ["chat", *stranger, "--session", sid.group(1), "--script", write_script(["hi"], "b.txt")],
    )
    assert (
        result.exit_code == 1 and "not found" in result.stderr
    )  # a 404, like an id that never existed
