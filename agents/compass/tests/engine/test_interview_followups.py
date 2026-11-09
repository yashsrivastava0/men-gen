"""Follow-up fixes on the AI-planned interview (task compass-ai-interview-followup).

* when the interview stops, every must/should need that has a catalog pattern default is assumed and listed
  in "We assumed" (the planner decides what to ask; it never leaves a downstream consumer without a visible answer);
* deciding an assumption that another answer already settled is a notice, not an error;
* list slots store the human label of a card option, not its internal code.

BUG-27 (a question still on screen comes back after a side message) is pinned by scenario SB-QP-12-b."""

from __future__ import annotations

import dataclasses

from mde_compass.catalog.loader import load_catalog
from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.engine.brief_builder import slots_from_brief
from mde_compass.engine.expr import holds
from mde_compass.engine.service import Service
from mde_compass.engine.slots import SlotStore
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

CATALOG = load_catalog()
IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


def planner_service() -> Service:
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview)
    return build_service(memory=True, clock=FakeClock(), compass_config=config)


async def finish(conv: Conversation, svc: Service, limit: int = 40) -> str:
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


async def test_stopping_assumes_every_pattern_default_for_must_and_should_needs() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
    store = SlotStore(CATALOG, clock=FakeClock())
    store.load(slots_from_brief(brief, CATALOG, FakeClock()))
    wanted = [
        sid
        for sid, sd in CATALOG.slots.items()
        if sd.default is not None
        and sd.level in ("must", "should")
        and sd.group != "org"
        and holds(sd.ask_if, store.all())
    ]
    assert wanted, "the catalog has must/should needs with a pattern default"
    assert [sid for sid in wanted if not store.filled(sid)] == []
    listed = {a.slot_id for a in brief.assumptions if a.slot_id}
    for sid in wanted:
        rec = store.get(sid)
        assert rec is not None
        if (
            rec.status == "assumed"
        ):  # a value the person gave stays theirs; an assumed one is listed
            assert sid in listed, sid


async def test_an_assumption_another_answer_already_settled_is_a_notice_not_an_error() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief.model_copy(deep=True)
        assert brief.assumptions, "the interview ends with visible assumptions"
        settled = brief.assumptions[0]
        brief.assumptions = [a for a in brief.assumptions if a.id != settled.id]
        brief.ids.tombstone(
            settled.id
        )  # what sync_assumptions does when the person answers that slot
        brief.version += 1
        await uow.briefs.save_version(conv.session_id, brief)
    before = len(conv.events)
    await conv.decide(settled.id, "accept")
    codes = [e.payload.get("code") for e in conv.events[before:] if e.type == "notice"]
    assert "assumption_settled" in codes
    assert not any(e.type == "error" for e in conv.events[before:])


def test_list_slots_store_the_label_of_a_card_option_not_its_code() -> None:
    svc = planner_service()
    checked = 0
    for sid, sd in CATALOG.slots.items():
        if sd.value_schema.kind not in ("object_list", "text_list"):
            continue
        for opt in sd.fallback_card.options:
            if not opt.value or "_" not in str(opt.value):
                continue
            label = opt.label.get("en")
            assert svc._label_codes(sid, [opt.value], "en") == [label]
            if sd.value_schema.kind == "object_list":
                assert svc._label_codes(sid, [{"name": opt.value, "intent": "x"}], "en") == [
                    {"name": label, "intent": "x"}
                ]
            checked += 1
    assert checked, "some list slot offers an option with an internal code"


def test_enum_slots_keep_their_codes() -> None:
    svc = planner_service()
    for sid, sd in CATALOG.slots.items():
        if sd.value_schema.kind in ("enum", "enum_list"):
            for opt in sd.fallback_card.options:
                if opt.value:
                    assert svc._label_codes(sid, opt.value, "en") == opt.value
                    return
    raise AssertionError("no enum slot with an option found")
