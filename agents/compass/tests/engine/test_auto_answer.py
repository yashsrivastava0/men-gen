"""Auto-answer (ADR 0016) through the real Service with the batch form: "You decide" on ONE question, and "Decide the
rest for me" for ALL of them."""

from __future__ import annotations

from mde_compass.compose import build_service
from mde_compass.domain import Card
from mde_compass.domain.events import BatchAnswer, BatchAnswerItem
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


def form(conv: Conversation) -> tuple[str, list[Card]]:
    payload = [e.payload for e in conv.events if e.type == "question_batch"][-1]
    cards = [Card.model_validate(c) for g in payload["groups"] for c in g["cards"]]
    return str(payload["batch_id"]), cards


def answer(card: Card) -> BatchAnswerItem:
    if card.options:
        opt = next((o for o in card.options if o.recommended), card.options[0])
        return BatchAnswerItem(
            card_id=card.id, value=[opt.id] if card.type == "pick_many" else opt.id
        )
    return BatchAnswerItem(card_id=card.id, custom_text=LONG)


async def started() -> tuple[Service, Conversation]:
    svc = build_service(memory=True, clock=FakeClock())
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    return svc, conv


async def stored(svc: Service, conv: Conversation):  # type: ignore[no-untyped-def]
    async with svc.store.begin("t1") as uow:
        return (
            (await uow.briefs.get(conv.session_id)).brief,
            await uow.qa.list(conv.session_id),
            await uow.facts.list(conv.session_id),
        )


async def test_you_decide_on_one_question_takes_the_recommended_option_and_lists_it() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    option_card = next(c for c in cards if c.options)
    decided = (
        next(o for o in option_card.options if o.recommended)
        if any(o.recommended for o in option_card.options)
        else option_card.options[0]
    )
    items = [
        BatchAnswerItem(card_id=c.id, action="you_decide") if c.id == option_card.id else answer(c)
        for c in cards
    ]
    await conv.send(BatchAnswer(event_id=conv._id(), batch_id=batch_id, answers=items))
    brief, qa, facts = await stored(svc, conv)
    status = {q.card_id: q.status for q in qa}
    assert status[option_card.id] == "you_decide"
    assert all(status[c.id] == "answered" for c in cards if c.id != option_card.id)
    label = decided.label.replace("[mock]", "").strip()
    assert any(
        label in a.claim and a.decision == "proposed" for a in brief.assumptions
    )  # visible, to approve
    decided_facts = [
        f for f in facts if f.origin == "assumption" and f.qa_card_id == option_card.id
    ]
    assert (
        len(decided_facts) == 1
        and not decided_facts[0].quote
        and label in decided_facts[0].statement
    )
    assert not decided_facts[0].supports_locked()  # an assumption is never Locked
    assert not [e for e in conv.events if e.type == "error"]


async def test_decide_the_rest_decides_everything_left_and_ends_the_interview() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    await conv.send(
        BatchAnswer(
            event_id=conv._id(), batch_id=batch_id, answers=[answer(cards[0])], decide_rest=True
        )
    )
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    brief, qa, _ = await stored(svc, conv)
    status = {q.card_id: q.status for q in qa}
    assert status[cards[0].id] == "answered"
    assert all(
        status[c.id] == "you_decide" for c in cards[1:]
    )  # the blank ones are decided, not skipped
    assert not [q for q in qa if q.status == "open"]  # nothing is left to ask
    assert brief.meta.interview is not None and brief.meta.interview.stop_reason
    assert len(brief.assumptions) >= len(cards) - 1  # every decision is listed in "We assumed"
    assert not [e for e in conv.events if e.type == "error"]


async def test_deciding_everything_from_the_first_batch_still_gives_a_reviewable_brief() -> None:
    svc, conv = await started()
    batch_id, _ = form(conv)
    await conv.send(
        BatchAnswer(event_id=conv._id(), batch_id=batch_id, answers=[], decide_rest=True)
    )
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    brief, qa, _ = await stored(svc, conv)
    assert brief.assumptions and not [q for q in qa if q.status == "open"]
    ready = [e for e in conv.events if e.type == "brief_ready"]
    assert (
        ready and ready[-1].payload["version"] >= 1
    )  # the person reviews and approves each assumption


async def test_untouched_ai_suggestion_submits_as_default_assumption() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    first_card = cards[0]
    opt_val = first_card.options[0].id if first_card.options else "tapped option"

    # Submit with ai_suggested = True (untouched AI suggestion)
    item = BatchAnswerItem(
        card_id=first_card.id,
        action="answer",
        value=opt_val,
        ai_suggested=True,
    )
    items = [item] + [answer(c) for c in cards[1:]]
    await conv.send(BatchAnswer(event_id=conv._id(), batch_id=batch_id, answers=items))

    brief, _qa, facts = await stored(svc, conv)
    decided_facts = [f for f in facts if f.origin == "assumption" and f.qa_card_id == first_card.id]
    assert decided_facts, "Untouched AI suggestion must be recorded with origin='assumption'"
    assert not decided_facts[0].supports_locked(), "Assumption is Default tier, never Locked"
    assert any(
        first_card.id in str(a) or (first_card.options and first_card.options[0].label in a.claim)
        for a in brief.assumptions
    ), "Visible in brief.assumptions for user review"
