"""``mde chat``: the whole command through Typer's runner (``--local --memory``: the real stack in
mock mode, no database, no key, no network)."""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mde_cli import __version__
from mde_cli.main import app
from mde_spec_loader import verify_hash

Script = Callable[[list[str]], str]


def chat(runner: CliRunner, projects: Path, *args: str, input: str | None = None) -> Result:
    return runner.invoke(
        app,
        ["chat", "--local", "--memory", "--projects-dir", str(projects), *args],
        input=input,
    )


def fake_key() -> str:
    return "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- the journey


def test_a_guided_journey_from_hi_to_spec_ready_has_the_transcript_shape(
    runner: CliRunner, tmp_path: Path, write_script: Script, journey: list[str]
) -> None:
    result = chat(runner, tmp_path / "projects", "--script", write_script(journey))
    out = result.stdout
    assert result.exit_code == 0, result.output
    assert out.startswith("In-process API, mock mode (no key, no network). State: in memory")
    assert re.search(r"^Session ses_\w{26} \(guided\)\.$", out, re.M)
    assert "\n> hi\n" in out and "> An online shop for handmade candles\n" in out  # the script echo
    # cards are numbered choices with a hint line; the suggested option is marked
    assert re.search(r"^Question 1 of \d+$", out, re.M)
    assert re.search(r"^  1\) .+$", out, re.M) and re.search(r"^  2\) .+$", out, re.M)
    assert "(suggested)" in out and "Type a number to choose" in out
    # the two gates are pages with the two buttons, in order, then spec_ready
    brief, plan, ready = (
        out.index(m) for m in ("=== Brief, version 1", "=== Plan, version 1", "Spec ready.")
    )
    assert brief < plan < ready
    assert out.count("  1) Looks right") == 2 and out.count("  2) Change something") == 2
    assert "Working on it" in out and re.search(r"^  \[(research|build)( \d+/\d+)?\] ", out, re.M)
    assert "-- now: PLANNING (research)" in out and "-- now: HANDED_OFF" in out
    # spec_ready: path, version, hash, commit; the file is in the hand-off repo and verifies
    shown = dict(re.findall(r"^  (path|repo|version|hash|commit): +(.+)$", out, re.M))
    assert shown["path"] == "APP_SPEC.md" and shown["version"] == "1"
    assert re.fullmatch(r"[0-9a-f]{64}", shown["hash"]) and re.fullmatch(
        r"[0-9a-f]{40}", shown["commit"]
    )
    spec = Path(shown["repo"]) / shown["path"]
    text = spec.read_bytes().decode("utf-8")
    assert verify_hash(text, shown["hash"])
    assert (Path(shown["repo"]) / "spec.lock.json").is_file()
    assert re.search(
        r"-- script finished: session ses_\w+ is HANDED_OFF \(spec_ready: 1, problems: 0\)", out
    )
    assert "Traceback" not in result.output


def test_finish_with_smart_defaults_skips_the_questions(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    lines = ["An online shop for handmade candles", "finish", "1", "1"]
    result = chat(runner, tmp_path / "p", "--script", write_script(lines))
    assert result.exit_code == 0, result.output
    assert result.stdout.count("Question ") == 1  # the one question the idea led to
    assert "Spec ready." in result.stdout and "HANDED_OFF" in result.stdout


def test_a_pasted_secret_is_masked_in_the_echo_and_never_printed_back(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    key = fake_key()
    lines = ["hi", f"by the way my key is {key}, use it for payments", "/status"]
    result = chat(runner, tmp_path / "p", "--script", write_script(lines))
    assert result.exit_code == 0, result.output
    assert key not in result.output and key not in result.stdout
    assert "> by the way my key is [REDACTED], use it for payments" in result.stdout
    assert "That looked like a password or key" in result.stdout  # the API's notice


def test_a_hinglish_user_gets_hinglish_hints_when_the_language_is_followed(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    lines = ["mujhe salon ka app chahiye, booking aur reminders ke saath"]
    result = chat(runner, tmp_path / "p", "--script", write_script(lines), "--lang", "hi-Latn")
    assert result.exit_code == 0, result.output
    assert "Vikalp chunne ke liye number likhein" in result.stdout


def test_an_enterprise_plan_waits_for_the_named_approvers(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    lines = ["A company app for our field staff: checklists and approvals", "r*", "1", "1"]
    result = chat(
        runner,
        tmp_path / "p",
        "--mode",
        "enterprise",
        "--policy",
        "enterprise-it-services-firm",
        "--script",
        write_script(lines),
    )
    out = result.stdout
    assert result.exit_code == 0, result.output
    assert "(enterprise)" in out and "  1) Send for approval" in out
    assert "Approvals: client_product_owner [waiting], delivery_lead [waiting]" in out
    assert "Sent for approval to: client_product_owner, delivery_lead" in out
    assert "PLAN_REVIEW (spec_ready: 0" in out and "Spec ready." not in out


def test_comments_and_blank_lines_in_a_script_are_skipped(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    lines = ["# a comment", "", "hi", "   ", "# another"]
    result = chat(runner, tmp_path / "p", "--script", write_script(lines))
    assert result.exit_code == 0 and result.stdout.count("\n> ") == 1


def test_an_unusable_line_in_a_script_makes_the_run_exit_1(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    lines = ["An online shop for handmade candles", "99"]
    result = chat(runner, tmp_path / "p", "--script", write_script(lines))
    assert result.exit_code == 1
    assert "Please pick a number from 1 to" in result.stdout and "problems: 1)" in result.stdout


def test_a_script_can_come_from_standard_input(runner: CliRunner, tmp_path: Path) -> None:
    result = chat(runner, tmp_path / "p", "--script", "-", input="hi\nquit\n")
    assert result.exit_code == 0 and "\n> hi\n" in result.stdout


# --------------------------------------------------------------------------- interactive


def test_an_interactive_chat_reads_the_keyboard_and_says_how_to_come_back(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = chat(runner, tmp_path / "p", input="An online shop for handmade candles\nr\nquit\n")
    assert result.exit_code == 0, result.output
    assert "Question 1 of" in result.stdout and "Question 2 of" in result.stdout
    assert re.search(r"Session ses_\w+ is in state DISCOVERING\.", result.stdout)
    assert "Come back to it with: mde chat --session ses_" in result.stdout
    assert "(Type help for commands, quit to leave.)" in result.stdout


def test_end_of_input_ends_an_interactive_chat_cleanly(runner: CliRunner, tmp_path: Path) -> None:
    result = chat(runner, tmp_path / "p", input="")
    assert result.exit_code == 0 and "Come back to it with" in result.stdout


# --------------------------------------------------------------------------- failures are friendly


def test_option_values_are_checked_before_anything_starts(
    runner: CliRunner, tmp_path: Path, write_script: Script
) -> None:
    script = write_script(["hi"])
    cases = [
        (["--mode", "wizard"], "--mode must be guided or enterprise"),
        (["--lang", "fr"], "--lang must be auto or one of"),
        (["--tenant", "bad tenant!"], "--tenant may use letters"),
        (["--user", "bad user!"], "--user may use letters"),
        (["--roles", "ok,not ok!"], "is not a valid role name"),
        (["--script", str(tmp_path / "missing.txt")], "cannot read the script"),
    ]
    for extra, message in cases:
        result = runner.invoke(app, ["chat", "--local", "--memory", "--script", script, *extra])
        assert result.exit_code == 2, (extra, result.output)
        assert message in result.stderr and "Traceback" not in result.output


def test_an_unreachable_api_is_one_line_with_a_hint_and_exit_1(
    runner: CliRunner, write_script: Script
) -> None:
    url = f"http://127.0.0.1:{free_port()}"
    result = runner.invoke(
        app, ["chat", "--url", url, "--token", "t", "--script", write_script(["hi"])]
    )
    assert result.exit_code == 1
    assert f"error: Cannot connect to the API at {url}." in result.stderr
    assert "hint: Start it with `just dev`" in result.stderr and "--local" in result.stderr
    assert "Traceback" not in result.output


def test_an_unexpected_failure_is_a_line_not_a_stack_trace_unless_asked(
    runner: CliRunner, write_script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("secret detail that must not be shown")

    monkeypatch.setattr("mde_cli.chat.run_loop", boom)
    argv = ["chat", "--local", "--memory", "--script", write_script(["hi"])]
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "unexpected problem (RuntimeError)" in result.stderr
    assert "secret detail" not in result.output and "Traceback" not in result.output
    monkeypatch.setenv("MDE_DEBUG", "1")
    again = runner.invoke(app, argv)
    assert isinstance(again.exception, RuntimeError)


# --------------------------------------------------------------------------- the entry points


def test_version_and_help() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).stdout.strip() == f"mde {__version__}"
    helped = runner.invoke(app, ["--help"])
    for command in ("chat", "sim", "budget", "doctor", "spec"):
        assert command in helped.stdout
    chat_help = runner.invoke(app, ["chat", "--help"]).stdout
    for option in (
        "--url",
        "--token",
        "--tenant",
        "--user",
        "--roles",
        "--mode",
        "--policy",
        "--lang",
        "--local",
        "--script",
    ):
        assert option in chat_help
    assert "dev-token" not in chat_help  # the default token is never shown


def test_python_dash_m_runs_the_same_command() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "mde_cli", "--version"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0 and done.stdout.strip() == f"mde {__version__}"
