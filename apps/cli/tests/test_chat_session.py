"""``ChatSession`` against the in-process API: what each kind of input does and what it prints."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest

from mde_cli.chat import HELP, ChatOptions, ChatSession
from mde_cli.client import ApiClient, Identity
from mde_cli.console import Out
from mde_context_api.composition import AppState

SECRET_FREE_IDEA = "An online shop for handmade candles"


def make_session(api: ApiClient, **options: object) -> tuple[ChatSession, io.StringIO]:
    buffer = io.StringIO()
    session = ChatSession(api, Out(buffer), ChatOptions(script=True, wait_s=30, **options))  # type: ignore[arg-type]
    return session, buffer


async def to_brief_gate(session: ChatSession) -> None:
    await session.open()
    assert await session.handle(SECRET_FREE_IDEA)
    assert await session.handle("r*")
    assert session.conv.state == "BRIEF_REVIEW"


async def test_opening_prints_the_session_line_before_the_greeting(api: ApiClient) -> None:
    session, out = make_session(api, mode="guided")
    await session.open()
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("Session ses_") and "(guided)" in lines[0]
    assert lines[1].startswith("Assistant: ")


async def test_a_message_gets_a_numbered_card_and_a_tap_moves_on(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    text = out.getvalue()
    assert "Question 1 of" in text and "\n  1) " in text and "(suggested)" in text
    assert "Type a number to choose" in text
    await session.handle("1")
    assert "Question 2 of" in out.getvalue()
    assert session.problems == 0


async def test_a_bad_number_prints_a_hint_and_sends_nothing(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    before = (await session.conv.refresh()).last_seq
    await session.handle("9")
    assert "Please pick a number from 1 to" in out.getvalue()
    assert (await session.conv.refresh()).last_seq == before
    assert session.problems == 1  # an unusable input counts, so a script run exits 1


async def test_d_and_s_answer_with_you_decide_and_skip(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    await session.handle("d")
    await session.handle("s")
    assert out.getvalue().count("Question ") >= 3 and session.problems == 0


async def test_the_hints_follow_the_session_language_or_the_option(api: ApiClient) -> None:
    session, out = make_session(api, lang="hi")
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    assert "विकल्प चुनने के लिए नंबर लिखें" in out.getvalue()
    assert session.language == "hi"
    auto, _ = make_session(api)
    assert auto.language is None  # before a session exists: nothing to follow


async def test_the_whole_conversation_to_spec_ready_and_a_closing_hint(api: ApiClient) -> None:
    session, out = make_session(api)
    await to_brief_gate(session)
    shown = out.getvalue()
    assert "=== Brief, version 1" in shown and "  1) Looks right" in shown
    await session.handle("1")
    assert session.conv.state == "PLAN_REVIEW"
    assert "Working on it" in out.getvalue() and "[build" in out.getvalue()
    await session.handle("1")
    final = out.getvalue()
    assert session.conv.state == "HANDED_OFF" and "Spec ready." in final
    assert "The spec is handed off. Type a change to keep going, or quit." in final
    assert final.count("The spec is handed off.") == 1  # once, not after every later input
    assert session.problems == 0


async def test_two_changes_the_gate_asks_for_the_text_on_the_next_line(api: ApiClient) -> None:
    session, out = make_session(api)
    await to_brief_gate(session)
    await session.handle("2")
    assert session.awaiting_change and "What would you like to change?" in out.getvalue()
    assert (await session.conv.refresh()).state == "BRIEF_REVIEW"
    await session.handle("make the look bold and dark")
    assert not session.awaiting_change
    assert session.problems == 0


async def test_two_with_the_text_on_the_same_line_is_one_step(api: ApiClient) -> None:
    session, out = make_session(api)
    await to_brief_gate(session)
    await session.handle("2 call the app Wick and Wax")
    assert not session.awaiting_change and "What would you like to change?" not in out.getvalue()


async def test_a_command_cancels_a_pending_change(api: ApiClient) -> None:
    session, _ = make_session(api)
    await to_brief_gate(session)
    await session.handle("2")
    assert session.awaiting_change
    assert await session.handle("help")
    assert not session.awaiting_change


async def test_words_at_a_gate_that_are_not_1_or_2_are_a_message(api: ApiClient) -> None:
    session, _ = make_session(api)
    await to_brief_gate(session)
    seq = (await session.conv.refresh()).last_seq
    await session.handle("yes please, looks good")
    assert (await session.conv.refresh()).last_seq > seq


async def test_help_status_and_unknown_commands(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    assert await session.handle("help") and all(line in out.getvalue() for line in HELP)
    await session.handle("/status")
    assert "DISCOVERING" in out.getvalue()
    await session.handle("/frobnicate")
    assert "I do not know the command /frobnicate" in out.getvalue()
    assert await session.handle("   ")  # blank input is ignored
    assert not await session.handle("quit")
    assert not await session.handle("/exit")


async def test_the_documents_commands_show_the_brief_the_plan_and_the_spec(api: ApiClient) -> None:
    session, out = make_session(api)
    await to_brief_gate(session)
    await session.handle("/plan")  # no plan yet: a problem, not a crash
    assert "Problem: There is no plan yet." in out.getvalue() and session.problems == 1
    before = out.getvalue().count("=== Brief")
    await session.handle("/brief")
    assert out.getvalue().count("=== Brief") == before + 1
    await session.handle("1")
    await session.handle("/plan")
    await session.handle("1")
    await session.handle("/spec")
    assert "Spec version 1, status handed_off" in out.getvalue()
    assert "---" not in out.getvalue().split("Spec version 1")[1].split("\n")[1]  # a summary only
    await session.handle("/spec full")
    assert "schema_id" in out.getvalue() or "spec_version" in out.getvalue()
    await session.handle("/status")
    assert "HANDED_OFF" in out.getvalue()


async def test_stop_pause_resume_and_restart_are_ui_actions(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    await session.handle("stop")  # nothing is running
    assert "Nothing is running" in out.getvalue()
    await session.handle("pause")
    assert session.conv.state == "PAUSED"
    await session.handle("resume")
    assert session.conv.state == "DISCOVERING"
    await session.handle("restart")
    await session.handle("/undo")
    assert session.problems == 0


async def test_finish_takes_smart_defaults_straight_to_the_brief(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    await session.handle(SECRET_FREE_IDEA)
    await session.handle("finish")
    assert session.conv.state == "BRIEF_REVIEW" and "=== Brief" in out.getvalue()


async def test_an_upload_sends_a_text_file_and_refuses_what_it_cannot_read(
    api: ApiClient, tmp_path: Path
) -> None:
    session, out = make_session(api)
    await session.open()
    notes = tmp_path / "notes.md"
    notes.write_text("We sell handmade candles online and want an app for it.", encoding="utf-8")
    seq = (await session.conv.refresh()).last_seq
    await session.handle(f'/upload "{notes}"')
    assert (await session.conv.refresh()).last_seq > seq and session.problems == 0
    await session.handle("/upload")
    assert "Usage: /upload FILE" in out.getvalue()
    await session.handle(f"/upload {tmp_path / 'missing.txt'}")
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01\x80")
    await session.handle(f"/upload {binary}")
    assert out.getvalue().count("I could not read") == 2 and session.problems == 2
    big = tmp_path / "big.txt"
    big.write_text("x" * 450_000, encoding="utf-8")
    await session.handle(f"/upload {big}")
    assert "is too large to send" in out.getvalue() and session.problems == 3


async def test_an_error_from_the_api_is_one_friendly_line(api: ApiClient) -> None:
    session, out = make_session(api)
    await session.open()
    session.conv.session_id = "ses_does_not_exist"
    await session.handle("hello")
    text = out.getvalue()
    assert "Problem:" in text and "Traceback" not in text
    assert session.problems == 1 and not session.fatal


async def test_resuming_shows_where_the_session_stands(api: ApiClient) -> None:
    first, _ = make_session(api)
    await first.open()
    await first.handle(SECRET_FREE_IDEA)
    again, out = make_session(api, session=first.conv.session_id)
    await again.open()
    shown = out.getvalue()
    assert f"Resumed session {first.conv.session_id} (guided, DISCOVERING)." in shown
    assert "Question 1 of" in shown and "Assistant:" not in shown  # no replay of the past
    gated, _ = make_session(api)
    await to_brief_gate(gated)
    resumed, resumed_out = make_session(api, session=gated.conv.session_id)
    await resumed.open()
    assert "=== Brief, version 1" in resumed_out.getvalue()
    assert "  1) Looks right" in resumed_out.getvalue()


async def test_resuming_a_session_that_is_planning_or_handed_off_says_so(
    api: ApiClient, state: AppState
) -> None:
    session, _ = make_session(api)
    await to_brief_gate(session)
    await state.stop_worker()
    await session.handle("1")  # the job is queued and nobody runs it: a short wait times out
    session.options.wait_s = 0.3
    await session.handle("/wait")
    planning, out = make_session(api, session=session.conv.session_id)
    await planning.open()
    assert "The plan is being prepared. Type /wait to watch it." in out.getvalue()
    state.start_worker()
    await planning.handle("/wait")
    assert planning.conv.state in ("PLANNING", "PLAN_REVIEW")


async def test_waiting_that_runs_out_tells_the_user_how_to_come_back(
    api: ApiClient, state: AppState
) -> None:
    session, out = make_session(api)
    await to_brief_gate(session)
    await state.stop_worker()
    session.options.wait_s = 0.3
    await session.handle("1")
    assert "Still planning after 0 seconds" in out.getvalue()
    assert f"--session {session.conv.session_id}" in out.getvalue()


async def test_a_pasted_secret_is_masked_in_the_echo_and_not_repeated_by_the_reply(
    api: ApiClient,
) -> None:
    session, out = make_session(api)
    await session.open()
    key = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    session.echo(f"my key is {key}")
    await session.handle(f"my key is {key}")
    text = out.getvalue()
    assert key not in text and "my key is [REDACTED]" in text
    assert "removed it before saving" in text  # the notice that the API sent


async def test_losing_the_connection_ends_a_script_but_not_an_interactive_chat() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://down.test")
    client = ApiClient(
        http, "t", Identity("tenant-a", "user-1"), label="the API at http://down.test"
    )
    for script, keep_going in ((True, False), (False, True)):
        buffer = io.StringIO()
        session = ChatSession(client, Out(buffer), ChatOptions(script=script))
        session.conv.session_id = "ses_1"
        assert await session.handle("hello") is keep_going
        assert "Problem: Cannot connect to the API at http://down.test" in buffer.getvalue()
        assert "Hint:" in buffer.getvalue() and session.fatal is script
    await client.aclose()


async def test_goodbye_names_the_session_and_how_to_come_back(api: ApiClient) -> None:
    session, out = make_session(api)
    session.goodbye()  # no session yet: nothing to say
    assert out.getvalue() == ""
    await session.open()
    session.goodbye()
    assert f"mde chat --session {session.conv.session_id}" in out.getvalue()


@pytest.mark.parametrize("mode", ["guided", "enterprise"])
async def test_both_modes_open(api: ApiClient, mode: str) -> None:
    session, out = make_session(api, mode=mode)
    await session.open()
    assert f"({mode})" in out.getvalue().splitlines()[0]
