"""The tiers through the real Service (ADR 0016): the person's tier sets the numbers, a planned batch is charged at
once (BUG-19), the hard cap holds, and the page is told where the interview stands."""

from __future__ import annotations

import dataclasses

import pytest

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain import Card
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"
TIERS = {
    "quick": (3, 7, 10),
    "standard": (4, 12, 15),
    "thorough": (5, 25, 30),
}  # batch, target, hard cap


def planner_service() -> Service:
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    return build_service(
        memory=True,
        clock=FakeClock(),
        compass_config=dataclasses.replace(DEFAULT_CONFIG, interview=interview),
    )


def batches(conv: Conversation) -> list[dict[str, object]]:
    return [e.payload for e in conv.events if e.type == "question_batch"]


def question_cards(conv: Conversation) -> list[Card]:
    seen: dict[str, Card] = {}
    for ev in conv.events:
        if ev.type == "card":
            card = Card.model_validate(ev.payload["card"])
            if card.origin != "frame":
                seen.setdefault(card.id, card)
    return list(seen.values())


async def drive(conv: Conversation, svc: Service, limit: int = 60) -> str:
    """Answer every card (taps where there are options, a long typed answer otherwise) until the Brief review."""
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


@pytest.mark.parametrize("tier", ["quick", "standard", "thorough"])
async def test_the_chosen_tier_sets_the_batch_size_and_is_reported_to_the_page(tier: str) -> None:
    batch_size, target, hard_cap = TIERS[tier]
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command(f"budget_{tier}")
    await conv.say(IDEA)
    (first,) = batches(conv)
    assert first["total"] == batch_size
    assert (
        first["progress"]
        == {
            "tier": tier,
            "target": target,
            "hard_cap": hard_cap,
            "committed": batch_size,  # the waiting questions of a planned batch are already charged (BUG-19)
            "closing": False,
        }
    )
    async with svc.store.begin("t1") as uow:
        block = (await uow.briefs.get(conv.session_id)).brief.meta.interview
    assert block is not None and (block.target, block.hard_cap, block.tier_set) == (
        target,
        hard_cap,
        True,
    )


async def test_without_a_chosen_tier_the_interview_is_standard_and_says_it_was_not_chosen() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    async with svc.store.begin("t1") as uow:
        block = (await uow.briefs.get(conv.session_id)).brief.meta.interview
    assert block is not None and (block.budget, block.target, block.tier_set) == (
        "standard",
        12,
        False,
    )


@pytest.mark.parametrize("tier", ["quick", "standard", "thorough"])
async def test_a_whole_interview_never_asks_more_than_the_hard_cap(tier: str) -> None:
    _, target, hard_cap = TIERS[tier]
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command(f"budget_{tier}")
    await conv.say(IDEA)
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    asked = question_cards(conv)
    assert len(asked) <= hard_cap
    base = [c for c in asked if c.origin != "followup"]
    assert (
        len(base) <= target
    )  # base questions stop at the target (the closing question is one of them)
    async with svc.store.begin("t1") as uow:
        block = (await uow.briefs.get(conv.session_id)).brief.meta.interview
    assert block is not None and block.questions_asked <= hard_cap
