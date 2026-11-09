"""End to end through the real Service in mock mode: idea to Brief, Gate 1, changes, undo, hand-off."""

from __future__ import annotations

import json

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


async def test_silence_never_approves_and_stale_hash_is_refused(service) -> None:
    conv = await _to_review(service)
    assert await conv.state() == "BRIEF_REVIEW"  # no approval without a gate decision
    await conv.approve(stale=True)
    assert await conv.state() == "BRIEF_REVIEW"
    assert not conv.of_type("brief_approved")


async def test_approval_binds_the_hash_and_hands_off_without_a_transcript(service) -> None:
    conv = await _to_review(service)
    for a in (await conv.brief())["assumptions"]:
        await conv.decide(a["id"], "accept")
    await conv.approve()
    assert await conv.state() == "APPROVED", [e.payload for e in conv.of_type("notice")][-3:]
    approved = conv.of_type("brief_approved")[-1].payload
    handoff = await service.handoff(conv.caller, conv.session_id)
    assert handoff["brief_hash"] == approved["brief_hash"]
    dump = json.dumps(handoff["views"])
    # The Brief content (the idea in the user's words) is handed over; the transcript machinery never is.
    for forbidden in ("provenance", '"quote"', '"history"', "turn_id", "evt_"):
        assert forbidden not in dump, forbidden
    assert {"research", "blueprint", "design", "verify"} <= set(handoff["views"])
    assert len(service.handoff_port.delivered) == 1  # delivered once, after Gate 1 only


async def test_event_ids_are_idempotent(service) -> None:
    from mde_compass.domain.events import UserMessage

    conv = await Conversation.start(service)
    ev = UserMessage(event_id="evt_dup1", text=IDEA)
    first = await conv.send(ev)
    again = await conv.send(ev)
    assert not first.duplicate and again.duplicate and again.events == []


async def test_a_wording_edit_is_a_new_version_and_undo_restores_the_content_hash(service) -> None:
    conv = await _to_review(service)
    before = await conv.brief()
    await conv.edit("idea.summary", "A food delivery app for Pune restaurants and their regulars")
    patches = conv.of_type("brief_patch")
    assert patches and patches[-1].payload["to_version"] == patches[-1].payload["from_version"] + 1
    changed = await conv.brief()
    assert changed["_hash"] != before["_hash"] and changed["version"] > before["version"]
    await conv.command("undo")
    restored = await conv.brief()
    assert restored["_hash"] == before["_hash"]  # content restored...
    assert (
        restored["version"] > changed["version"]
    )  # ...as a NEW version: history is never rewritten


async def test_a_geography_change_reseeds_research(service) -> None:
    conv = await _to_review(service)
    before = (await conv.brief())["research_requests"]
    await conv.edit("context.geo", {"country": "India", "city": "Nagpur"})
    after = (await conv.brief())["research_requests"]
    assert after and [r["id"] for r in after] != [
        r["id"] for r in before
    ]  # re-seeded, ids never reused


async def test_declined_category_is_refused_early_with_an_alternative(service) -> None:
    conv = await Conversation.start(service)
    await conv.say("I want to build a 3D multiplayer racing game")
    notices = [n.payload["text"] for n in conv.of_type("notice")]
    assert any("game" in t.lower() or "3d" in t.lower() for t in notices), notices
    assert await conv.state() in {"WELCOME", "UNDERSTANDING", "INTERVIEWING", "PLAYBACK"}


async def test_ambiguity_findings_become_non_blocking_questions_and_change_no_value(
    service,
) -> None:
    from dataclasses import replace

    from mde_compass.engine.ports import AmbiguityFinding

    class Scanner:
        async def scan(self, ctx, brief):
            return [
                AmbiguityFinding(
                    slot_id=None, text="What does 'fast delivery' mean for you?", kind="vague"
                ),
                AmbiguityFinding(
                    slot_id=None, text="what does 'fast delivery' mean for you?", kind="vague"
                ),
            ]

    service.bind_ports(replace(service.ports, ambiguity_scanner=Scanner()))
    conv = await _to_review(service)
    brief = await conv.brief()
    questions = [q for q in brief["open_questions"] if "fast delivery" in q["text"]]
    assert (
        len(questions) == 1 and questions[0]["blocking"] is False
    )  # de-duplicated, never blocking
    ready = conv.of_type("brief_ready")[-1].payload
    assert not any("fast delivery" in b for b in ready["blockers"])  # it cannot hold up Gate 1
