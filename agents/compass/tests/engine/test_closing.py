"""The closing question (ADR 0016): the pure helpers, and the guarantees through the real Service."""

from __future__ import annotations

import dataclasses

import pytest

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain import Card
from mde_compass.engine import closing
from mde_compass.engine.service import Service
from mde_compass.scenarios.doubles import make_card
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


# ------------------------------------------------------------------------------------------------ pure helpers
def test_names_come_from_records_strings_and_single_values() -> None:
    assert closing.names_of([{"name": "Order"}, "Track", {"label": "Pay"}, {"x": 1}, " "]) == [
        "Order",
        "Track",
        "Pay",
    ]
    assert closing.names_of("One") == ["One"] and closing.names_of(None) == []


def test_the_context_keeps_stated_features_first_and_never_repeats_them_as_suggestions() -> None:
    ctx = closing.build_context(
        features=[{"name": "Order"}, {"name": "Track"}],
        suggested_features=["track", "Pay", "Pay", "Rate"],
        roles=[{"name": "Customer"}],
        goals=["Grow", ["Save time"]],
        constraints=["Hindi and English"],
        assumptions=["  ", "Payments are online"],
    )
    assert ctx.features == ["Order", "Track"]
    assert ctx.suggested_features == ["Pay", "Rate"]  # "track" is already stated; "Pay" once
    assert ctx.roles == ["Customer"] and ctx.goals == ["Grow", "Save time"]
    assert ctx.constraints == ["Hindi and English"] and ctx.assumptions == ["Payments are online"]


def test_the_context_is_short_statements_never_the_whole_ledger() -> None:
    many = [f"Feature {i}" for i in range(40)]
    ctx = closing.build_context(features=many, suggested_features=many)
    assert len(ctx.features) == closing.MAX_PER_LIST and ctx.suggested_features == []


def _card() -> Card:
    return make_card(
        "Q-020",
        "What matters most for the first version of your tiffin app?",
        type="priority",
        origin="closing",
        needs=["scope.priority"],
        options=["Order a lunch", "Track the rider", "Pay online", "Rate the meal"],
    )


def test_the_picks_become_must_in_order_and_the_other_options_nice_never_later() -> None:
    card = _card()
    placed = closing.placement(card, ["B", "A"])
    assert placed is not None
    assert placed.priorities == {
        "Order a lunch": "must",
        "Track the rider": "must",
        "Pay online": "nice",
        "Rate the meal": "nice",
    }
    assert "later" not in set(placed.priorities.values())
    over = closing.placement(card, ["A", "B", "C", "D"], cap=2)
    assert over is not None and [n for n, b in over.priorities.items() if b == "must"] == [
        "Order a lunch",
        "Track the rider",
    ]


def test_an_empty_or_junk_answer_never_reprioritises() -> None:
    card = _card()
    for junk in (None, [], "", "Z", ["Z", "Q"], {"nice": ["A"]}):
        assert closing.placement(card, junk) is None
    assert (
        closing.placement(card, {"must": ["C"]}) is not None
    )  # the bucket form: the must bucket is the picks


def test_priority_is_set_on_the_persons_own_features_by_name_and_nothing_else_changes() -> None:
    placed = closing.placement(_card(), ["A"])
    assert placed is not None
    features = [
        {"name": "order a lunch", "intent": "x", "status": "assumed"},
        {"name": "Pay online", "intent": "y"},
        {"name": "Something else", "intent": "z"},
    ]
    out = closing.apply_to_features(features, placed)
    assert out[0]["priority"] == "must" and "status" not in out[0] and out[0]["intent"] == "x"  # type: ignore[index]
    assert out[1]["priority"] == "nice"  # type: ignore[index]
    assert out[2] == {"name": "Something else", "intent": "z"}  # not on the card: untouched


# ------------------------------------------------------------------------------------------- through the Service
def planner_service() -> Service:
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    return build_service(
        memory=True,
        clock=FakeClock(),
        compass_config=dataclasses.replace(DEFAULT_CONFIG, interview=interview),
    )


def asked(conv: Conversation) -> list[Card]:
    seen: dict[str, Card] = {}
    for ev in conv.events:
        if ev.type == "card":
            card = Card.model_validate(ev.payload["card"])
            if card.frame == "planned":
                seen.setdefault(card.id, card)
    return list(seen.values())


async def drive(conv: Conversation, svc: Service, limit: int = 80) -> str:
    for _ in range(limit):
        state = (await svc.get_session(conv.caller, conv.session_id)).state
        card = conv.last_card()
        if state not in ("INTERVIEWING", "PLAYBACK", "UNDERSTANDING") or card is None:
            return state
        if card.options:
            await conv.tap_recommended()
        else:
            await conv.answer(LONG, card=card)
    return (await svc.get_session(conv.caller, conv.session_id)).state


async def block_of(svc: Service, conv: Conversation):  # type: ignore[no-untyped-def]
    async with svc.store.begin("t1") as uow:
        return (await uow.briefs.get(conv.session_id)).brief.meta.interview


@pytest.mark.parametrize("tier", ["quick", "standard", "thorough"])
async def test_every_interview_ends_with_exactly_one_closing_question_and_it_is_last(
    tier: str,
) -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command(f"budget_{tier}")
    await conv.say(IDEA)
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    cards = asked(conv)
    closing_cards = [c for c in cards if c.origin == "closing"]
    assert len(closing_cards) == 1 and cards[-1].id == closing_cards[0].id
    assert closing_cards[0].type == "priority" and not closing_cards[0].fallback
    block = await block_of(svc, conv)
    assert block is not None and block.closing == "answered"
    flagged = [
        e.payload["progress"]["closing"]
        for e in conv.events
        if e.type == "question_batch" and e.payload.get("progress")
    ]
    assert (
        flagged[-1] is True and flagged.count(True) == 1
    )  # the page is told which batch is the last one


async def test_saying_enough_decides_the_closing_instead_of_asking_it() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    await conv.say("that's enough, please use defaults")
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    assert not [
        c for c in asked(conv) if c.origin == "closing"
    ]  # never put to someone who said enough
    block = await block_of(svc, conv)
    assert block is not None and block.closing == "auto"


async def test_skipping_the_closing_question_decides_it_and_still_ends_the_interview() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    for _ in range(80):
        card = conv.last_card()
        if card is not None and card.origin == "closing":
            break
        assert card is not None
        await (conv.tap_recommended() if card.options else conv.answer(LONG, card=card))
    else:
        raise AssertionError("the closing question never came")
    await conv.skip()
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    block = await block_of(svc, conv)
    assert block is not None and block.closing == "auto"


async def test_ask_me_more_reopens_and_the_longer_interview_ends_with_its_own_closing_question() -> (
    None
):
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command("budget_quick")
    await conv.say(IDEA)
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    first = [c for c in asked(conv) if c.origin == "closing"]
    assert len(first) == 1
    await conv.command("ask_more")
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    cards = asked(conv)
    closing_cards = [c for c in cards if c.origin == "closing"]
    assert len(closing_cards) == 2 and cards[-1].id == closing_cards[-1].id
    block = await block_of(svc, conv)
    assert block is not None and block.reopened == 1 and block.closing == "answered"
    assert len(cards) <= 30  # the Thorough hard cap holds for the whole, extended interview
