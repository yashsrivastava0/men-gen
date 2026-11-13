"""The end of the interview (ADR 0016): the Brief is compiled BEFORE research is seeded and the probes run, so both
read the full Brief, and the review step's second compile is a cached no-op. Changing a slot-less assumption keeps
the person's words."""

from __future__ import annotations

import dataclasses
from typing import Any

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain import Card
from mde_compass.domain.events import BatchAnswer, BatchAnswerItem
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


def spy(svc: Service, port: str, method: str, log: list[str], label: str) -> None:
    """Record every call of ``ports.<port>.<method>`` in order (the real call still runs)."""
    assert svc.ports is not None
    target = getattr(svc.ports, port)
    real = getattr(target, method)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        log.append(label)
        return await real(*args, **kwargs)

    class Spy:
        def __getattr__(self, name: str) -> Any:
            return getattr(target, name)

    inst = Spy()
    setattr(inst, method, wrapper)
    object.__setattr__(svc.ports, port, inst)


async def test_the_brief_is_compiled_before_research_is_seeded_and_the_probes_run() -> None:
    svc = card_by_card_service()
    log: list[str] = []
    spy(svc, "brief_compiler", "compile_section", log, "compile")
    spy(svc, "seeder", "seed", log, "seed")
    spy(svc, "prober", "probe", log, "probe")
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await drive(conv, svc) == "BRIEF_REVIEW"
    assert "compile" in log and "seed" in log and "probe" in log
    assert log.index("compile") < log.index("seed") < log.index("probe")
    # the review step compiled again, and an unchanged ledger made no new model call
    first_probe = log.index("probe")
    assert log[:first_probe].count("compile") >= 1
    assert log[first_probe:].count("compile") == 0


async def test_changing_a_decided_assumption_keeps_the_persons_words() -> None:
    svc = build_service(memory=True, clock=FakeClock())
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    payload = [e.payload for e in conv.events if e.type == "question_batch"][-1]
    cards = [Card.model_validate(c) for g in payload["groups"] for c in g["cards"]]
    option_card = next(c for c in cards if c.options)
    items = [
        BatchAnswerItem(card_id=c.id, action="you_decide")
        if c.id == option_card.id
        else BatchAnswerItem(card_id=c.id, custom_text=LONG)
        for c in cards
    ]
    await conv.send(BatchAnswer(event_id=conv._id(), batch_id=payload["batch_id"], answers=items))
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
    decided = next(a for a in brief.assumptions if a.slot_id is None and a.decision == "proposed")
    await conv.decide(decided.id, "change", value="Use WhatsApp messages to update customers")
    assert not [e for e in conv.events if e.type == "error"]
    async with svc.store.begin("t1") as uow:
        after = (await uow.briefs.get(conv.session_id)).brief
    row = next(a for a in after.assumptions if a.id == decided.id)
    assert row.decision == "changed"
    assert (
        "whatsapp" in str(after.model_dump(mode="json")).lower()
    )  # the words reached the Brief, not the bin
