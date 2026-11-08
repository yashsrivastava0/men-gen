"""GAP-2: silence on a pending card (``UiAction(action="idle")``): one gentle nudge, then assume.

Spec 3.4 "Assume policy": "You decide", skip, or silence after one nudge use the recommended option,
mark it ``assumed``, give a one-line reason and continue. Spec section 9, "User ignores a card":
one nudge, then assume. The client reports silence; code decides what it means.
"""

from __future__ import annotations

from mde_contracts.events import AssistantMessage, CardEvent
from mde_discovery import copy as texts
from mde_discovery.testing import Conversation


def messages(conv: Conversation) -> list[str]:
    return [e.text for e in conv.last.events if isinstance(e, AssistantMessage)]


async def shop_at_apps(conv: Conversation | None = None) -> Conversation:
    conv = conv or Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()  # confirm the type
    assert conv.pending is not None and conv.pending.card.id == "Q-apps-01"
    return conv


# ---------------------------------------------------------------------------- the first silence


async def test_the_first_idle_nudges_once_and_shows_the_same_card_again() -> None:
    conv = await shop_at_apps()
    card = conv.pending
    assert card is not None
    calls = conv.gateway.count()
    result = await conv.ui("idle")
    assert [type(e) for e in result.events] == [AssistantMessage, CardEvent]
    assert messages(conv) == [texts.say("nudge", "en")]
    assert "Take your time" in messages(conv)[0]
    [shown] = [e for e in result.events if isinstance(e, CardEvent)]
    assert shown.card.instance_id == card.instance_id  # the very same card, not a new one
    assert conv.pending is not None and conv.pending.instance_id == card.instance_id
    assert conv.state_of("scope.apps") == "empty"  # nothing is assumed yet
    assert conv.state.nudged_card_ids == [card.card.id]
    assert conv.gateway.count() == calls  # a nudge costs no LLM call
    assert result.llm_calls == 0 and result.intent == "other"
    assert result.changed_slots == []


async def test_idle_is_not_a_counted_turn() -> None:
    conv = await shop_at_apps()
    turns = conv.state.turn_count
    await conv.ui("idle")
    await conv.ui("idle")
    assert conv.state.turn_count == turns


# --------------------------------------------------------------------------- the second silence


async def test_the_second_idle_assumes_the_recommended_option_with_a_reason() -> None:
    conv = await shop_at_apps()
    await conv.ui("idle")
    result = await conv.ui("idle")
    rec = conv.slot("scope.apps")
    assert rec is not None and rec.state == "assumed" and rec.source == "default"
    assert rec.value == ["customer"] and rec.evidence  # one-line reason kept
    text = " ".join(messages(conv))
    assert "No problem, I will go with" in text and rec.evidence in text
    assert conv.pending is not None and conv.pending.card.id == "Q-feat-01"  # on to the next
    assert conv.state.skips_in_row == 1  # silence counts as a skip
    assert result.changed_slots == ["scope.apps"]
    assert conv.state.nudged_card_ids == []  # the next card starts fresh


async def test_each_card_gets_its_own_nudge() -> None:
    conv = await shop_at_apps()
    await conv.ui("idle")
    await conv.ui("idle")  # assumed: the features card appears
    await conv.ui("idle")  # silence on the new card: a nudge, not an assumption
    assert messages(conv) == [texts.say("nudge", "en")]
    assert conv.pending is not None and conv.pending.card.id == "Q-feat-01"
    assert conv.state_of("scope.features") == "empty"


async def test_an_answer_between_the_two_silences_resets_the_nudge() -> None:
    conv = await shop_at_apps()
    await conv.ui("idle")
    assert conv.state.nudged_card_ids == ["Q-apps-01"]
    await conv.tap_recommended()  # the user answers
    assert conv.state.nudged_card_ids == []
    assert conv.pending is not None and conv.pending.card.id == "Q-feat-01"
    await conv.ui("idle")  # a fresh card: only a nudge
    assert conv.state_of("scope.features") == "empty"
    assert conv.state.nudged_card_ids == ["Q-feat-01"]


async def test_the_nudge_is_forgotten_when_the_same_card_is_asked_again() -> None:
    conv = await shop_at_apps()
    await conv.ui("idle")
    await conv.tap_recommended()  # answered
    await conv.ui("edit_row", "scope.apps")  # the user reopens the same card later
    assert conv.pending is not None and conv.pending.card.id == "Q-apps-01"
    await conv.ui("idle")
    assert messages(conv) == [texts.say("nudge", "en")]  # a nudge again, not an instant assumption


async def test_idle_after_chit_chat_nudge_assumes() -> None:
    """The off-topic path and silence share one rule: one nudge per card, then assume."""
    conv = await shop_at_apps()
    await conv.say("lol")
    await conv.say("haha nice")  # the chit-chat nudge
    assert conv.state.nudged_card_ids == ["Q-apps-01"]
    await conv.ui("idle")  # silence after that nudge: assume
    assert conv.state_of("scope.apps") == "assumed"


async def test_three_silences_in_a_row_still_offer_finish_with_defaults() -> None:
    conv = await shop_at_apps()
    for _ in range(3):
        await conv.ui("idle")
        await conv.ui("idle")  # nudge + assume, three times
    assert conv.state.skips_in_row == 3
    assert conv.pending is not None and conv.pending.card.id == "Q-nudge-02"
    assert {o.id for o in conv.pending.card.options} == {"finish", "continue"}
    await conv.ui("idle")  # the offer is nudged once, never answered for the user
    await conv.ui("idle")
    assert conv.pending is not None and conv.pending.card.id == "Q-nudge-02"
    assert not conv.last.brief_complete


async def test_idle_after_the_interview_is_complete_does_nothing() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()
    await conv.ui("finish_with_defaults")
    assert conv.last.brief_complete and conv.pending is None
    result = await conv.ui("idle")  # nothing is pending: nothing happens
    assert result.events == [] and result.brief_complete


# ------------------------------------------------------------------------- nothing to nudge


async def test_idle_with_no_pending_card_is_a_silent_no_op() -> None:
    conv = Conversation()
    await conv.start()
    assert conv.pending is None
    before = conv.state
    result = await conv.ui("idle")
    assert result.events == [] and result.llm_calls == 0
    assert conv.state == before


async def test_idle_at_an_open_gate_or_in_a_review_state_never_does_anything() -> None:
    conv = await shop_at_apps()
    for session_state, gate in (("BRIEF_REVIEW", "brief"), ("PLAN_REVIEW", "plan")):
        conv.session_state = session_state
        conv.gate_open = gate  # type: ignore[assignment]
        before = conv.state
        result = await conv.ui("idle")
        assert result.events == [] and conv.state == before
        assert conv.state_of("scope.apps") == "empty"
    conv.gate_open = None
    conv.session_state = "PLANNING"
    result = await conv.ui("idle")
    assert result.events == []


# --------------------------------------------------------------- cards that are never assumed


async def test_a_help_card_is_nudged_once_and_then_left_alone() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("I don't know")
    assert conv.pending is not None and conv.pending.card.kind == "help"
    first = await conv.ui("idle")
    assert any(isinstance(e, AssistantMessage) for e in first.events)
    again = await conv.ui("idle")
    assert again.events == []  # a help card is never answered for the user
    assert conv.pending is not None and conv.pending.card.kind == "help"
    assert conv.state_of("idea.archetype") == "empty"


async def test_a_must_confirm_card_gets_a_waiting_nudge_and_is_never_assumed() -> None:
    conv = Conversation(policy="regulated-bank")
    await conv.start()
    await conv.say("An online shop for a client")
    await conv.tap_recommended()  # confirm the type
    for _ in range(12):  # walk to the first must-confirm card (compliance or SSO)
        card = conv.pending
        assert card is not None
        sd = conv.engine.selector.slot_def(card.card.slot)
        if conv.mode in sd.must_confirm_in:
            break
        await conv.tap_recommended()
    card = conv.pending
    assert card is not None
    slot = card.card.slot
    first = await conv.ui("idle")
    [nudge] = [e.text for e in first.events if isinstance(e, AssistantMessage)]
    assert nudge == texts.say("nudge_wait", "en") and "choose for you" not in nudge
    second = await conv.ui("idle")
    assert second.events == []
    assert conv.pending is not None and conv.pending.card.slot == slot
    assert conv.state_of(slot) == "empty"


async def test_the_idle_nudge_follows_the_session_language() -> None:
    for lang in ("hi", "hi-Latn"):
        conv = Conversation(language_hint=lang)
        await conv.start()
        await conv.say(next(iter(conv.engine.banks.chips("commerce", lang))).label)
        await conv.tap_recommended()
        await conv.ui("idle")
        assert messages(conv) == [texts.say("nudge", lang)]
        assert messages(conv)[0] != texts.say("nudge", "en")
        await conv.ui("idle")
        head = texts.say("assumed_ack", lang, option="@@", reason="##").split("@@")[0]
        assert any(m.startswith(head) for m in messages(conv))
        rec = conv.slot("scope.apps")
        assert rec is not None and rec.state == "assumed"
