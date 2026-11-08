"""Every LLM call has a deterministic fallback: Discover never surfaces a raw error."""

from __future__ import annotations

import pytest

from mde_contracts.calls import C1Output, C14Output
from mde_contracts.events import AssistantMessage, CardEvent, Notice
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    ProviderError,
    StructuredOutputError,
)
from mde_discovery.llm import FALLBACKS, use_the_buttons
from mde_discovery.testing import Conversation, ScriptedGateway, make_call_context
from mde_prompts.registry import load_spec

ERRORS: list[BaseException] = [
    StructuredOutputError("invalid after repair"),
    BudgetExceeded("cap reached"),
    CircuitOpen("open"),
    ProviderError("503", status=503, retryable=True),
    TimeoutError("slow"),
    TimeoutError(),
]
CALLS = (
    "C1.understand",
    "C2.interpret_brief",
    "C3.respond",
    "C5.write_card",
    "C11.write_summary",
    "C14.interpret_change",
)


def broken(call_id: str, error: BaseException) -> Conversation:
    gateway = ScriptedGateway()
    gateway.fail_always(call_id, error)
    return Conversation(gateway=gateway)


def codes(conv: Conversation) -> list[str]:
    return [e.code for e in conv.last.events if isinstance(e, Notice)]


def test_every_spec_names_a_fallback_the_engine_registers() -> None:
    for call_id in CALLS:
        assert load_spec(call_id).fallback in FALLBACKS, call_id
    assert set(FALLBACKS) == {load_spec(c).fallback for c in CALLS}


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_c1_failure_falls_back_to_the_keyword_understander(error: BaseException) -> None:
    conv = broken("C1.understand", error)
    await conv.start()
    await conv.say("I want an online shop with cart and reviews")
    assert conv.value("idea.archetype") == "commerce"  # the keyword understander read it
    assert "taking_longer" in codes(conv)
    assert conv.pending is not None and conv.pending.card.id == "Q-type-01"
    assert conv.gateway.count("C1.understand") >= 1


async def test_a_message_the_fallback_cannot_place_gets_i_did_not_catch_that_and_the_options() -> (
    None
):
    conv = broken("C1.understand", StructuredOutputError("bad"))
    await conv.start()
    await conv.say("I want an online shop")
    card = conv.pending
    assert card is not None
    await conv.say("qwerty zxcvb")
    assert conv.messages()[-1] == "I did not catch that. Here are the options."
    assert conv.pending is not None and conv.pending.instance_id == card.instance_id
    assert any(isinstance(e, CardEvent) for e in conv.last.events)  # the options again


async def test_the_first_message_falls_back_to_help_when_nothing_can_be_read() -> None:
    conv = broken("C1.understand", BudgetExceeded("cap"))
    await conv.start()
    await conv.say("zzzz")
    assert conv.pending is not None and conv.pending.card.id == "Q-help-01"  # options, not an error


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_c2_failure_falls_back_to_the_keyword_archetype_match(error: BaseException) -> None:
    conv = broken("C2.interpret_brief", error)
    await conv.start()
    await conv.say("I want a salon app where customers book appointments")
    assert conv.value("idea.archetype") == "booking_services"
    assert conv.slot("idea.summary") is not None
    assert "taking_longer" in codes(conv)


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_c3_failure_falls_back_to_a_canned_reply_from_the_knowledge(
    error: BaseException,
) -> None:
    conv = broken("C3.respond", error)
    await conv.start()
    await conv.say("An online shop")
    await conv.say("how much does it cost?")
    assert "pricing details" in conv.messages()[-1]  # the cost snippet itself
    await conv.say("which database do you use?")
    assert "will not guess" in conv.messages()[-1]
    assert conv.pending is not None and "taking_longer" in codes(conv)
    await conv.say("lol")
    assert conv.messages()[-1]  # a canned chit-chat line, never empty


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_c11_failure_falls_back_to_a_template_sentence(error: BaseException) -> None:
    conv = await finished(broken("C11.write_summary", error))
    brief = await conv.engine.compile_brief(conv.request(), _ctx())
    assert brief.app_summary.endswith(".") and "online shop" in brief.app_summary.lower()
    assert len(brief.rows) == 6


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_c14_failure_asks_the_user_to_use_the_buttons_or_edit_the_row(
    error: BaseException,
) -> None:
    conv = broken("C14.interpret_change", error)
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()
    await conv.say("change the look to bold dark")
    assert "use the buttons" in conv.messages()[-1] and "edit the row" in conv.messages()[-1]
    assert conv.slot("design.direction") is None  # nothing was guessed
    assert conv.pending is not None


async def test_c5_failure_falls_back_to_a_card_built_from_the_slots_own_options() -> None:
    gateway = ScriptedGateway()
    gateway.fail_always("C5.write_card", StructuredOutputError("bad"))
    conv = Conversation(gateway=gateway)
    await conv.start()
    await conv.say("An online shop")
    for _ in range(2):
        await conv.tap_recommended()
    # reopening a slot the bank has no card for (Guided has no logo card) calls C5
    card, generated = await conv.engine._card_for_slot(  # pyright: ignore[reportPrivateUsage]
        _turn(conv), "brand.assets"
    )
    assert generated and card.type == "text" and card.slot == "brand.assets"
    assert card.text and card.id == "Q-gen-brand-assets"


async def test_a_cancelled_call_is_not_swallowed() -> None:
    conv = broken("C1.understand", CallCancelled("stop"))
    await conv.start()
    with pytest.raises(CallCancelled):
        await conv.say("I want an online shop")


async def test_no_error_ever_reaches_the_user_when_every_call_fails() -> None:
    gateway = ScriptedGateway()
    for call_id in CALLS:
        gateway.fail_always(call_id, ProviderError("down", retryable=True))
    conv = Conversation(gateway=gateway)
    await conv.start()
    await conv.say("I want a salon app where customers book appointments")
    await conv.say("how much does it cost?")
    await conv.drive_recommended()
    assert conv.last.brief_complete
    await conv.say("change the look to bold dark")
    for event in conv.events:
        if isinstance(event, AssistantMessage | Notice):
            text = event.text
            assert "Error" not in text and "Traceback" not in text and "503" not in text
    brief = await conv.engine.compile_brief(conv.request(), _ctx())
    assert brief.app_summary


def test_the_buttons_fallback_is_localised_and_never_guesses() -> None:
    from mde_contracts.calls import C14Input

    for lang, needle in (("en", "buttons"), ("hi", "बटन"), ("hi-Latn", "buttons")):
        out = use_the_buttons(C14Input(message="x", language=lang, state="BRIEF_REVIEW"))
        assert isinstance(out, C14Output) and not out.understood and out.ops == []
        assert out.clarify_question and needle in out.clarify_question


def test_the_keyword_fallbacks_are_the_same_functions_as_the_mock_handlers() -> None:
    from mde_contracts.calls import C1Input

    out = FALLBACKS["discovery.keyword_understander"](C1Input(message="hello"))
    assert isinstance(out, C1Output) and out.intent == "greeting"


async def test_the_scripted_gateway_can_refuse_unscripted_calls() -> None:
    gateway = ScriptedGateway(fallback_to_mock=False)
    spec = load_spec("C3.respond")
    from mde_contracts.calls import C3Input

    with pytest.raises(AssertionError):
        await gateway.run(spec, C3Input(message="x", intent="chitchat"), make_call_context())


# ----------------------------------------------------------------------------- helpers


async def finished(conv: Conversation) -> Conversation:
    await conv.start()
    await conv.say("An online shop")
    await conv.drive_recommended()
    assert conv.last.brief_complete
    return conv


def _ctx():  # type: ignore[no-untyped-def]
    from mde_discovery.testing import make_run_context

    return make_run_context()


def _turn(conv: Conversation):  # type: ignore[no-untyped-def]
    from mde_discovery.slots import SlotStore
    from mde_discovery.turn import Turn

    req = conv.request()
    return Turn(
        req=req,
        ctx=_ctx(),
        state=req.state,
        store=SlotStore(req.state.slots, conv.clock.now),
        ids=conv.ids,
        now=conv.clock.now,
    )
