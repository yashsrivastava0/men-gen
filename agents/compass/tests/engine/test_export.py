"""The full output of a session for the next engine (the research agent): complete, read-only, plain JSON."""

from __future__ import annotations

import dataclasses
import json

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


def card_by_card_service() -> Service:
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    return build_service(
        memory=True,
        clock=FakeClock(),
        compass_config=dataclasses.replace(DEFAULT_CONFIG, interview=interview),
    )


async def drive(conv: Conversation, svc: Service, limit: int = 80) -> str:
    for _ in range(limit):
        state = (await svc.get_session(conv.caller, conv.session_id)).state
        card = conv.last_card()
        if state not in ("INTERVIEWING", "PLAYBACK", "UNDERSTANDING") or card is None:
            return state
        await (conv.tap_recommended() if card.options else conv.answer(LONG, card=card))
    return (await svc.get_session(conv.caller, conv.session_id)).state


async def test_the_full_output_has_everything_and_matches_the_brief() -> None:
    svc = card_by_card_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    out = await svc.export_full(conv.caller, conv.session_id)
    json.dumps(out)  # plain JSON, nothing to convert
    assert out["export"]["format"] == "compass-full-output"  # type: ignore[index]
    assert out["export"]["brief_hash"] == out["brief"]["_hash"]  # type: ignore[index]
    assert set(out["consumer_views"]) == {"research", "blueprint", "design", "verify"}  # type: ignore[arg-type]
    interview = out["interview"]
    assert interview["qa"] and interview["facts"] and interview["batches"]  # type: ignore[index]
    assert all("statement" in f and "status" in f and "origin" in f for f in interview["facts"])  # type: ignore[index]
    assert out["brief_versions"] and out["session"]["id"] == conv.session_id  # type: ignore[index]
    assert out["spend"]["calls"] >= 0 and "sources" in out and "change_sets" in out  # type: ignore[operator]
    brief = await svc.get_brief(conv.caller, conv.session_id)
    assert out["brief"]["domain_facts"] == brief["domain_facts"]  # type: ignore[index]
