"""The small parts: the friendly wrapper, the loop runner, identity and token, script reading, output."""

from __future__ import annotations

import asyncio
import getpass
import io
import sys
from pathlib import Path

import pytest
import typer

from mde_cli import common
from mde_cli.client import ApiError, ApiUnreachable, Identity
from mde_cli.common import build_identity, default_user, read_script, resolve_token
from mde_cli.console import Out, harden_stdio
from mde_cli.errors import (
    EXIT_BAD_INPUT,
    EXIT_PROBLEM,
    CliError,
    api_error_text,
    fail,
    friendly,
)
from mde_cli.runtime import LoopRunner

# --------------------------------------------------------------------------- friendly


def run_friendly(raiser: BaseException) -> tuple[int, str]:
    @friendly
    def command() -> None:
        raise raiser

    with pytest.raises(typer.Exit) as raised:
        command()
    return raised.value.exit_code, ""


def test_every_expected_failure_is_one_line_and_an_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases: list[tuple[BaseException, int, str]] = [
        (
            CliError("nope", exit_code=EXIT_BAD_INPUT, hint="try this"),
            2,
            "error: nope\nhint: try this",
        ),
        (
            ApiUnreachable("Cannot connect.", hint="start it"),
            1,
            "error: Cannot connect.\nhint: start it",
        ),
        (ApiError(404, "not_found", "Not found."), 1, "error: Not found."),
        (
            ApiError(500, "internal_error", "Broke.", request_id="req_1"),
            1,
            "Broke. (request id req_1)",
        ),
        (KeyboardInterrupt(), 130, "interrupted"),
        (RuntimeError("secret detail"), 1, "unexpected problem (RuntimeError)"),
    ]
    for raiser, code, text in cases:
        assert run_friendly(raiser)[0] == code
        err = capsys.readouterr().err
        assert text in err and "Traceback" not in err and "secret detail" not in err


def test_exits_and_system_exits_pass_through_untouched() -> None:
    @friendly
    def leave() -> None:
        raise typer.Exit(7)

    with pytest.raises(typer.Exit) as raised:
        leave()
    assert raised.value.exit_code == 7

    @friendly
    def stop() -> None:
        raise SystemExit(5)

    with pytest.raises(SystemExit):
        stop()

    @friendly
    def fine() -> int:
        return 3

    assert fine() == 3


def test_debug_mode_re_raises_the_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_DEBUG", "1")

    @friendly
    def boom() -> None:
        raise ValueError("visible now")

    with pytest.raises(ValueError, match="visible now"):
        boom()
    assert common.debug_enabled()


def test_a_401_never_says_which_credential_was_wrong() -> None:
    text = api_error_text(ApiError(401, "unauthorized", "Missing or invalid credentials."))
    assert "--token" in text and "--tenant" in text and "--user" in text
    assert api_error_text(ApiError(418, "teapot", "")) == "the API answered 418"


def test_fail_writes_to_stderr_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as raised:
        fail("went wrong", exit_code=EXIT_PROBLEM)
    assert raised.value.exit_code == 1
    assert capsys.readouterr().err == "error: went wrong\n"


# --------------------------------------------------------------------------- the loop runner


def test_the_runner_keeps_one_loop_for_many_steps() -> None:
    runner = LoopRunner()
    try:

        async def loop_id() -> int:
            return id(asyncio.get_running_loop())

        assert runner.run(loop_id()) == runner.run(loop_id())
        assert isinstance(runner.loop, asyncio.SelectorEventLoop)
    finally:
        runner.close()
    assert runner.loop.is_closed()
    runner.close()  # closing twice is harmless


def test_ctrl_c_stops_the_step_and_leaves_nothing_running() -> None:
    runner = LoopRunner()
    try:
        cancelled: list[bool] = []

        async def interrupted() -> None:
            raise KeyboardInterrupt

        assert runner.run_interruptible(interrupted()) == (False, None)

        async def slow() -> str:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return "late"

        pending: list[asyncio.Task[str]] = []

        async def watcher() -> str:
            pending.append(asyncio.create_task(slow()))
            await asyncio.sleep(0)
            raise KeyboardInterrupt

        finished, value = runner.run_interruptible(watcher())
        assert (finished, value) == (False, None)
        runner.run(asyncio.gather(*pending, return_exceptions=True))  # the stray task is cleaned up

        async def quick() -> str:
            return "done"

        assert runner.run_interruptible(quick()) == (True, "done")
    finally:
        runner.close()


def test_closing_cancels_tasks_that_are_still_pending() -> None:
    runner = LoopRunner()
    seen: list[str] = []

    async def background() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    async def start() -> None:
        asyncio.get_running_loop().create_task(background())
        await asyncio.sleep(0)

    runner.run(start())
    runner.close()
    assert seen == ["cancelled"]


# --------------------------------------------------------------------------- identity, token, script


def test_the_default_user_is_a_valid_api_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getpass, "getuser", lambda: "Jane Doe")
    assert default_user() == "Jane-Doe"
    monkeypatch.setattr(getpass, "getuser", lambda: "***")
    assert default_user() == "cli-user"

    def no_user() -> str:
        raise OSError("no passwd entry")

    monkeypatch.setattr(getpass, "getuser", no_user)
    assert default_user() == "cli-user"
    assert build_identity("local", None).user == "cli-user"


def test_identity_roles_are_split_and_deduplicated() -> None:
    who = build_identity("tenant-a", "user-1", " delivery_lead , qa,delivery_lead,, ")
    assert who == Identity("tenant-a", "user-1", ("delivery_lead", "qa"))
    assert build_identity("tenant-a", "user-1").roles == ()


def test_a_token_comes_from_the_option_or_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_token("given") == "given"
    monkeypatch.setenv("API_DEV_TOKEN", "from-the-environment")
    assert resolve_token(None) == "from-the-environment"
    monkeypatch.delenv("API_DEV_TOKEN")
    assert resolve_token(None) == "dev-token"  # the dev default the API also uses


def test_a_script_is_read_as_utf8_with_or_without_a_bom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "a.txt"
    plain.write_text("hi\nनमस्ते\n", encoding="utf-8")
    assert read_script(str(plain)) == ["hi", "नमस्ते"]
    bom = tmp_path / "b.txt"
    bom.write_bytes("﻿hi\n".encode())
    assert read_script(str(bom)) == ["hi"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("one\ntwo\n"))
    assert read_script("-") == ["one", "two"]
    with pytest.raises(CliError) as missing:
        read_script(str(tmp_path / "missing.txt"))
    assert missing.value.exit_code == EXIT_BAD_INPUT
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(CliError, match="UnicodeDecodeError"):
        read_script(str(bad))


# --------------------------------------------------------------------------- output


def test_output_never_reads_brackets_as_markup_and_never_wraps() -> None:
    buffer = io.StringIO()
    out = Out(buffer)
    long = "word " * 80
    out.line("[REDACTED] and [bold]not bold[/bold]", "bold")
    out.line(long.strip())
    out.lines(["a", "b"])
    text = buffer.getvalue()
    assert "[REDACTED] and [bold]not bold[/bold]" in text
    assert long.strip() in text.splitlines()[1]  # one line, however long
    assert text.splitlines()[2:] == ["a", "b"] and not out.interactive


def test_the_error_line_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    Out().error("a problem")
    captured = capsys.readouterr()
    assert captured.err.strip() == "a problem" and captured.out == ""


def test_stdio_is_hardened_so_an_unencodable_character_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    harden_stdio()
    stream.write("नमस्ते")  # would raise UnicodeEncodeError on a strict ascii console
    stream.flush()
    assert raw.getvalue() == b"??????"
