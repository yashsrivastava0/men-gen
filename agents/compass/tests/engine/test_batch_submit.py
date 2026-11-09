"""Submit once (ADR 0016), through the real Service with the PRODUCT default layout (the batch form).

The person answers every question of the batch and submits ONCE; nothing is planned before that. A question the form
leaves out counts as skipped, a repeated submit changes nothing, "none of these" is recorded as an answer with no
content."""

from __future__ import annotations

import pytest

from mde_compass.compose import build_service
from mde_compass.domain import Card
from mde_compass.domain.events import BatchAnswer, BatchAnswerItem
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock
from mde_core.errors import NotFound

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


def product_service() -> Service:
    return build_service(
        memory=True, clock=FakeClock()
    )  # the default configuration: planner, batch form


def batches(conv: Conversation) -> list[dict[str, object]]:
    return [e.payload for e in conv.events if e.type == "question_batch"]


def form(conv: Conversation) -> tuple[str, list[Card]]:
    """The batch id and the questions of the latest batch the page was sent."""
    payload = batches(conv)[-1]
    cards = [
        Card.model_validate(c)
        for g in payload["groups"]  # type: ignore[attr-defined]
        for c in g["cards"]
    ]
    return str(payload["batch_id"]), cards


def item(card: Card, action: str = "answer") -> BatchAnswerItem:
    if action != "answer":
        return BatchAnswerItem(card_id=card.id, action=action)  # type: ignore[arg-type]
    if card.options:
        opt = next((o for o in card.options if o.recommended), card.options[0])
        return BatchAnswerItem(
            card_id=card.id, value=[opt.id] if card.type == "pick_many" else opt.id
        )
    return BatchAnswerItem(card_id=card.id, custom_text=LONG)


async def submit(conv: Conversation, batch_id: str, answers: list[BatchAnswerItem]):  # type: ignore[no-untyped-def]
    return await conv.send(BatchAnswer(event_id=conv._id(), batch_id=batch_id, answers=answers))


async def qa_status(svc: Service, conv: Conversation) -> dict[str, str]:
    async with svc.store.begin("t1") as uow:
        return {q.card_id: q.status for q in await uow.qa.list(conv.session_id)}


async def started() -> tuple[Service, Conversation]:
    svc = product_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    return svc, conv


async def test_the_product_default_is_the_form_and_every_question_is_on_screen_at_once() -> None:
    svc, conv = await started()
    (first,) = batches(conv)
    assert first["ui_mode"] == "answer_all" and first["total"] == 4
    _, cards = form(conv)
    status = await qa_status(svc, conv)
    assert [status[c.id] for c in cards] == ["open"] * 4
    async with svc.store.begin("t1") as uow:
        shown = [
            q.asked_at is not None
            for q in await uow.qa.list(conv.session_id)
            if q.card_id in {c.id for c in cards}
        ]
    assert all(shown)  # the whole batch is charged and on screen, not one question at a time


async def test_answering_some_of_the_cards_one_by_one_plans_nothing_new() -> None:
    svc, conv = await started()
    _, cards = form(conv)
    for card in cards[
        :3
    ]:  # a client that answers card by card: still nothing is planned until the batch is complete
        await conv.answer(None, custom=LONG, card=card)
    assert len(batches(conv)) == 1
    assert (await qa_status(svc, conv))[cards[3].id] == "open"
    assert not [e for e in conv.events if e.type == "error"]


async def test_one_submit_processes_every_answer_and_only_then_plans_the_next_batch() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    assert len(batches(conv)) == 1
    await submit(conv, batch_id, [item(c) for c in cards])
    status = await qa_status(svc, conv)
    assert all(status[c.id] != "open" for c in cards)  # the whole form is closed by the one event
    assert len(batches(conv)) >= 2  # and the next questions were planned from ALL of the answers
    next_id, next_cards = form(conv)
    assert next_id != batch_id and next_cards
    assert not [e for e in conv.events if e.type == "error"]


async def test_a_question_the_form_left_out_counts_as_skipped() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    await submit(conv, batch_id, [item(cards[0]), item(cards[1])])
    status = await qa_status(svc, conv)
    assert status[cards[0].id] == "answered" and status[cards[1].id] == "answered"
    assert status[cards[2].id] == "skipped" and status[cards[3].id] == "skipped"


async def test_a_repeated_submit_changes_nothing() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    answers = [item(c) for c in cards]
    await submit(conv, batch_id, answers)
    before = (len(batches(conv)), await qa_status(svc, conv), len(conv.events))
    await submit(conv, batch_id, answers)  # a retry after a slow reply, with a new event id
    after = (len(batches(conv)), await qa_status(svc, conv))
    assert after == before[:2]  # no second batch, nothing recorded twice
    assert not [e for e in conv.events if e.type == "error"]


async def test_none_of_these_is_an_answer_with_no_content_and_never_an_error() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    await submit(conv, batch_id, [item(cards[0], "none"), *[item(c) for c in cards[1:]]])
    status = await qa_status(svc, conv)
    assert status[cards[0].id] == "answered"
    async with svc.store.begin("t1") as uow:
        first = next(q for q in await uow.qa.list(conv.session_id) if q.card_id == cards[0].id)
    assert first.answer is None and not first.custom_text
    assert not [e for e in conv.events if e.type == "error"]


async def test_you_decide_on_one_question_of_the_form_is_recorded_for_that_question_only() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    await submit(conv, batch_id, [item(cards[0], "you_decide"), *[item(c) for c in cards[1:]]])
    status = await qa_status(svc, conv)
    assert status[cards[0].id] == "you_decide"
    assert all(status[c.id] == "answered" for c in cards[1:])


async def test_the_follow_ups_of_one_submit_arrive_as_one_form() -> None:
    svc, conv = await started()
    batch_id, cards = form(conv)
    await submit(conv, batch_id, [item(c) for c in cards])
    follow_id, follow_cards = form(conv)
    assert batches(conv)[-1]["mode"] == "followup" and follow_id != batch_id
    assert len(follow_cards) >= 2 and all(c.origin == "followup" for c in follow_cards)
    open_now = {k for k, v in (await qa_status(svc, conv)).items() if v == "open"}
    assert open_now == {c.id for c in follow_cards}  # every open question is on that one form


async def test_a_submit_for_a_batch_that_was_never_planned_is_a_not_found_error() -> None:
    svc, conv = await started()
    _, cards = form(conv)
    with pytest.raises(NotFound):  # the API answers 404 and the page offers to retry
        await submit(conv, "BAT-999", [item(cards[0])])
    assert (await qa_status(svc, conv))[cards[0].id] == "open"  # nothing was recorded
