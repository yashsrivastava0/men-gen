"""End to end through the real Service in mock mode: idea to Brief, Gate 1, changes, undo, hand-off."""

from __future__ import annotations

from mde_compass.catalog import load_catalog
from mde_compass.domain import Brief
from mde_compass.engine.validate import validate_brief
from mde_compass.testing import Conversation

IDEA = "I want a food delivery app for restaurants in Pune, in Hindi and English"


async def _to_review(service) -> Conversation:
    conv = await Conversation.start(service)
    await conv.say(IDEA)
    await conv.drive_to_review()
    return conv


async def test_welcome_then_idea_reaches_review_with_a_valid_brief(service) -> None:
    conv = await Conversation.start(service)
    assert conv.last_card() is not None and conv.last_card().frame == "welcome"
    await conv.say(IDEA)
    assert await conv.state() in {"INTERVIEWING", "PLAYBACK", "BRIEF_REVIEW"}
    assert conv.of_type("playback")
    await conv.drive_to_review()
    assert await conv.state() == "BRIEF_REVIEW"
    ready = conv.of_type("brief_ready")[-1].payload
    assert ready["brief_hash"]
    brief = Brief.model_validate(
        {k: v for k, v in (await conv.brief()).items() if not k.startswith("_")}
    )
    assert validate_brief(brief, load_catalog()) == []
    questions = [
        e
        for e in conv.of_type("card")
        if e.payload["card"]["frame"] not in ("welcome", "change_variants")
    ]
    assert len(questions) <= 10
