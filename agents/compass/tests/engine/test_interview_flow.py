"""The AI-planned interview through the real Service (mock providers, in-memory store).

The model proposes, code governs: these tests drive whole sessions and check what the person sees and what is
stored. ``engine == "planner"`` is set per test; the default stays ``legacy`` until the flip."""

from __future__ import annotations

import dataclasses

import pytest

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain import Card
from mde_compass.domain.events import Command
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
LONG = "We deliver 200 lunch boxes a day to offices, mostly in Baner and Hinjewadi, and riders are independent"


def planner_service() -> Service:
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview)
    return build_service(memory=True, clock=FakeClock(), compass_config=config)


def types(conv: Conversation, start: int = 0) -> list[str]:
    return [e.type for e in conv.events[start:]]


def cards_of(conv: Conversation) -> list[Card]:
    seen: dict[str, Card] = {}
    for ev in conv.events:
        if ev.type == "card":
            card = Card.model_validate(ev.payload["card"])
            seen.setdefault(card.id, card)
    return list(seen.values())


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


def test_the_default_engine_is_the_planner_and_legacy_is_a_rollback() -> None:
    assert DEFAULT_CONFIG.interview.engine == "planner"


async def test_round_zero_shows_the_playback_then_plans_the_first_batch_in_one_request() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    before = len(conv.events)
    await conv.say(IDEA)
    kinds = types(conv, before)
    # the playback card is committed first; an internal turn then plans the batch while the person reads it
    assert kinds.index("playback") < kinds.index("question_batch") < kinds.index("card")
    batch = next(e for e in conv.events if e.type == "question_batch")
    assert batch.payload["mode"] == "initial" and batch.payload["total"] >= 3
    first = conv.last_card()
    assert first is not None and first.origin == "planner" and not first.fallback
    assert first.batch_id == batch.payload["batch_id"] and first.need_ids


async def test_a_whole_session_reaches_review_with_planned_questions_and_no_fallback() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    cards = [c for c in cards_of(conv) if c.frame == "planned"]
    assert len(cards) >= 5
    assert all(c.origin in ("planner", "followup", "closing") and not c.fallback for c in cards)
    closing = [c for c in cards if c.origin == "closing"]
    assert (
        len(closing) == 1 and cards[-1].id == closing[0].id
    )  # one closing question, and it is the last
    questions = [c.question for c in cards]
    assert len(set(questions)) == len(questions)  # nothing is asked twice
    async with svc.store.begin("t1") as uow:
        batches = await uow.batches.list(conv.session_id)
        facts = await uow.facts.list(conv.session_id)
        qa = await uow.qa.list(conv.session_id)
        brief = (await uow.briefs.get(conv.session_id)).brief
    assert batches and not any(b.used_fallback for b in batches)
    assert (
        facts
        and all(f.source_ref for f in facts)
        and {f.id for f in facts} == {f"FCT-{i:03d}" for i in range(1, len(facts) + 1)}
    )
    assert {q.card_id for q in qa} >= {c.id for c in cards if c.id in {q.card_id for q in qa}}
    # every evidence-bearing slot cites a fact, and none is Locked on an unverified quote
    verified = {f.id for f in facts if f.supports_locked()}
    for section in (brief.idea, brief.context, brief.audience, brief.experience, brief.access):
        for key, item in section.items():
            if item.tier == "Locked":
                refs = {p.ref for p in item.provenance if p.is_evidence()}
                assert refs & verified or all(r.startswith("evt_") for r in refs), key
    assert brief.meta.interview is not None and brief.meta.interview.fallback_count == 0
    assert brief.meta.interview.questions_asked == len([q for q in qa if q.status != "dropped"])


async def test_a_vague_answer_to_an_important_need_earns_exactly_one_followup() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None and first.need_ids == ["audience.segments"]
    await conv.answer("office workers", card=first)  # vague: three words, no detail
    follow = conv.last_card()
    assert follow is not None and follow.origin == "followup"
    assert (
        follow.parent_card_id == first.id
        and follow.depth == 1
        and follow.need_ids == first.need_ids
    )
    await conv.answer(
        "office workers", card=follow
    )  # still vague: assumed visibly, never nagged again
    nxt = conv.last_card()
    assert (
        nxt is not None and nxt.id != follow.id and nxt.origin != "followup"
    ) or nxt.need_ids != first.need_ids
    assert (
        sum(1 for c in cards_of(conv) if c.origin == "followup" and c.need_ids == first.need_ids)
        == 1
    )
    assert any(e.type == "notice" and e.payload["code"] == "assumed" for e in conv.events)


async def test_a_specific_answer_gets_no_followup() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.answer(LONG, card=first)
    assert not any(c.origin == "followup" for c in cards_of(conv))


async def test_skip_and_you_decide_move_on_and_are_recorded() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.skip()
    second = conv.last_card()
    assert second is not None and second.id != first.id
    await conv.you_decide()
    async with svc.store.begin("t1") as uow:
        qa = {q.card_id: q for q in await uow.qa.list(conv.session_id)}
    assert qa[first.id].status == "skipped" and qa[second.id].status == "you_decide"
    assert conv.last_card() is not None and conv.last_card().id not in (first.id, second.id)  # type: ignore[union-attr]


async def test_answer_all_mode_shows_the_whole_batch_and_one_event_answers_it() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command("mode_answer_all")
    await conv.say(IDEA)
    batch = next(e for e in conv.events if e.type == "question_batch")
    assert batch.payload["ui_mode"] == "answer_all"
    cards = [c for g in batch.payload["groups"] for c in g["cards"]]
    assert len(cards) == batch.payload["total"] >= 3
    from mde_compass.domain.events import BatchAnswer, BatchAnswerItem

    answers = [
        BatchAnswerItem(card_id=c["id"], value=c["options"][0]["id"] if c["options"] else LONG)
        for c in cards
    ]
    before = len(conv.events)
    await conv.send(
        BatchAnswer(event_id="e_batch", batch_id=batch.payload["batch_id"], answers=answers)
    )
    async with svc.store.begin("t1") as uow:
        qa = {q.card_id: q for q in await uow.qa.list(conv.session_id)}
    assert all(qa[c["id"]].status == "answered" for c in cards)
    assert types(
        conv, before
    )  # the turn produced events (a follow-up, the next batch or the review)


async def test_switching_the_layout_mid_batch_shows_the_rest_of_the_batch_again() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.tap_recommended()  # one question answered, the rest of the batch is waiting
    before = len(conv.events)
    await conv.command("mode_answer_all")
    batch = next(e for e in conv.events[before:] if e.type == "question_batch")
    shown = [c["id"] for g in batch.payload["groups"] for c in g["cards"]]
    assert batch.payload["ui_mode"] == "answer_all"
    assert len(shown) >= 2 and first.id not in shown  # only what is still unanswered
    assert batch.payload["answered"] >= 1
    assert all(g["title"] for g in batch.payload["groups"] if g["id"])
    async with svc.store.begin("t1") as uow:
        qa = {q.card_id: q for q in await uow.qa.list(conv.session_id)}
    assert all(qa[i].status == "open" for i in shown)  # nothing was answered, dropped or reworded
    # and back again: one at a time, the first of them is on screen and the others wait their turn
    before = len(conv.events)
    await conv.command("mode_one_at_a_time")
    batch = next(e for e in conv.events[before:] if e.type == "question_batch")
    assert batch.payload["ui_mode"] == "one_at_a_time"
    cards = [e for e in conv.events[before:] if e.type == "card"]
    assert [c.payload["card"]["id"] for c in cards] == [shown[0]]
    before = len(conv.events)
    await conv.tap_recommended()
    assert conv.last_card() is not None and conv.last_card().id == shown[1]  # type: ignore[union-attr]
    # a second switch to the same layout changes nothing
    before = len(conv.events)
    await conv.command("mode_one_at_a_time")
    assert not [e for e in conv.events[before:] if e.type in ("question_batch", "card")]


@pytest.mark.parametrize("how", ["command", "words"])
async def test_finish_with_defaults_and_thats_enough_stop_the_interview(how: str) -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    await conv.tap_recommended()
    if how == "command":
        await conv.command("finish_with_defaults")
    else:
        await conv.say("that's enough")
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        qa = await uow.qa.list(conv.session_id)
        brief = (await uow.briefs.get(conv.session_id)).brief
    assert not [q for q in qa if q.status == "open"]  # nothing is left waiting
    assert brief.meta.interview is not None and brief.meta.interview.stop_reason
    assert brief.assumptions  # what was not asked is a visible assumption


async def test_resending_an_event_is_acknowledged_and_ignored() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    count = len(cards_of(conv))
    from mde_compass.domain.events import UserMessage

    event = UserMessage(event_id="e_dup", text=LONG)
    first = await svc.handle_event(conv.caller, conv.session_id, event)
    again = await svc.handle_event(conv.caller, conv.session_id, event)
    assert first.accepted and again.duplicate and not again.events
    assert len(cards_of(conv)) == count


async def test_the_chained_round_zero_turn_is_idempotent_by_construction() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    async with svc.store.begin("t1") as uow:
        events = await uow.events.list_history(conv.session_id, after_idx=0, limit=200)
    internal = [
        e for e in events.items if e.direction == "in" and (e.event_id or "").startswith("int_")
    ]
    assert len(internal) == 1 and internal[0].payload["name"] == "prepare_questions"
    # replaying the same internal command does nothing: its id is already recorded
    again = await svc.handle_event(
        conv.caller,
        conv.session_id,
        Command(event_id=internal[0].event_id or "", name="prepare_questions"),
    )
    assert again.duplicate


@pytest.mark.parametrize("name", ["budget_quick", "budget_standard", "budget_thorough"])
async def test_the_budget_commands_set_the_interview_depth(name: str) -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.command(name)
    await conv.say(IDEA)
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
    assert brief.meta.plan is not None
    assert brief.meta.plan.interview_budget == name.removeprefix("budget_")
    assert brief.meta.interview is not None and brief.meta.interview.budget == name.removeprefix(
        "budget_"
    )


async def test_the_brief_is_built_from_the_ledger_and_no_fact_is_lost() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
        facts = await uow.facts.list(conv.session_id)
    active = [f for f in facts if f.status == "active"]
    assert active, "the interview must have produced facts"
    placed = {d.id for d in brief.domain_facts}
    cited = {
        p.ref
        for section in (brief.idea, brief.context, brief.audience, brief.experience, brief.access)
        for item in section.values()
        for p in item.provenance
        if p.ref.startswith("FCT-")
    }
    not_used = {f.id for f in facts if f.status == "not_used"}
    # fact coverage: every active fact is placed in the Brief, cited by a slot, or marked not_used
    assert {f.id for f in active} <= placed | cited | not_used
    block = brief.meta.interview
    assert block is not None and block.coverage == 1.0 and block.facts_active == len(active)
    for row in brief.domain_facts:
        assert row.consumers and row.statement and row.id.startswith("FCT-")
        assert (row.tier == "Locked") == (row.id in {f.id for f in active if f.supports_locked()})


async def test_consumer_views_carry_the_facts_and_never_the_words_of_the_chat() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
        facts = [f for f in await uow.facts.list(conv.session_id) if f.status == "active"]
    import json

    from mde_compass.views.consumers import build_handoff

    handoff = json.dumps(build_handoff(brief), ensure_ascii=False)
    assert '"domain_facts"' in handoff
    assert '"quote"' not in handoff and "evt_" not in handoff
    for fact in facts:
        if fact.quote and len(fact.quote) > 25 and fact.quote not in fact.statement:
            assert fact.quote not in handoff  # the verbatim words never reach a consumer


def small_budget_service() -> Service:
    """Tiny context budgets, so the chunked compile and the rolling summary are exercised by a short session."""
    interview = dataclasses.replace(
        DEFAULT_CONFIG.interview, engine="planner", default_ui_mode="one_at_a_time"
    )
    context = dataclasses.replace(
        DEFAULT_CONFIG.context, compile_ceiling=60, compile_chunk_facts=4, fact_digest=60
    )
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview, context=context)
    return build_service(memory=True, clock=FakeClock(), compass_config=config)


async def test_an_oversized_ledger_is_compiled_per_group_in_small_batches_and_nothing_is_lost() -> (
    None
):
    """v2 (ADR 0016): one call per group of facts, cut into batches of ``compile_chunk_facts``; no whole-ledger
    consistency pass; every active fact still ends up as a detail, the evidence of a value, or not used."""
    svc = small_budget_service()
    assert svc.ports is not None
    calls: list[tuple[str, str, int]] = []
    real = svc.ports.brief_compiler.compile_section  # type: ignore[attr-defined]

    async def spy(ctx, request):  # type: ignore[no-untyped-def]
        calls.append((request.group, request.task, request.ledger.count("FCT-")))
        return await real(ctx, request)

    object.__setattr__(
        svc.ports, "brief_compiler", type("Spy", (), {"compile_section": staticmethod(spy)})()
    )
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    fills = [c for c in calls if c[1] == "fill" and c[0] != "place"]
    assert len(fills) >= 3 and len({g for g, *_ in fills}) >= 2  # several groups, several batches
    assert all(n <= 4 for _, _, n in fills)  # batches of at most four facts
    assert (
        sum(1 for c in calls if c[1] == "typical") == 1
    )  # one extra call drafts the typical details
    async with svc.store.begin("t1") as uow:
        brief = (await uow.briefs.get(conv.session_id)).brief
        facts = [f for f in await uow.facts.list(conv.session_id) if f.status == "active"]
    assert {f.id for f in facts} <= {d.id for d in brief.domain_facts} | {
        p.ref
        for sec in (brief.idea, brief.context, brief.audience, brief.experience, brief.access)
        for item in sec.values()
        for p in item.provenance
    }
    assert all(d.kind for d in brief.domain_facts)  # every row is typed by the kinds file


async def test_the_rolling_summary_is_kept_once_the_ledger_outgrows_the_digest_budget() -> None:
    svc = small_budget_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    assert await finish(conv, svc) == "BRIEF_REVIEW"
    async with svc.store.begin("t1") as uow:
        summary = await uow.summaries.get(conv.session_id)
        facts = await uow.facts.list(conv.session_id)
    assert summary is not None and summary.topics and summary.version >= 1
    summarised = {i for t in summary.topics for i in t.fact_ids}
    assert summarised  # facts reach the summary by id
    assert summarised <= {f.id for f in facts}


async def test_editing_an_answer_supersedes_its_facts_and_rebuilds_the_slots() -> None:
    from mde_compass.domain.events import AnswerEdit

    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None and first.need_ids == ["audience.segments"]
    await conv.answer(
        "Office workers in Baner and Hinjewadi who order a daily lunch box", card=first
    )
    async with svc.store.begin("t1") as uow:
        before = [f for f in await uow.facts.list(conv.session_id) if f.qa_card_id == first.id]
    assert before and all(f.status == "active" for f in before)

    await conv.send(
        AnswerEdit(
            event_id="e_edit",
            card_id=first.id,
            value=None,
            custom_text="Students and hostel residents near the college, who want cheap dinners every evening",
        )
    )
    async with svc.store.begin("t1") as uow:
        facts = [f for f in await uow.facts.list(conv.session_id) if f.qa_card_id == first.id]
        versions = [
            q
            for q in await uow.qa.list(conv.session_id, latest_only=False)
            if q.card_id == first.id
        ]
        brief = (await uow.briefs.get(conv.session_id)).brief
    # the old facts are kept but superseded; the new answer's facts are active; the history keeps both versions
    assert {f.id for f in before} <= {f.id for f in facts if f.status == "superseded"}
    assert any(f.status == "active" and "Students" in f.statement for f in facts)
    assert sorted((q.version, q.status) for q in versions) == [(1, "superseded"), (2, "answered")]
    seg = brief.audience["segments"]
    assert "Students" in str(seg.value) and "Office workers" not in str(seg.value)
    assert any(p.ref.startswith("FCT-") for p in seg.provenance)


async def test_an_answer_cannot_be_edited_after_approval() -> None:
    from mde_compass.domain.events import AnswerEdit

    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.answer(LONG, card=first)
    # an unknown or still-open card cannot be edited: a notice, no change
    before = len(conv.events)
    await conv.send(AnswerEdit(event_id="e_edit2", card_id="Q-999", value="x"))
    assert any(
        e.type == "notice" and e.payload["code"] == "edit_unavailable" for e in conv.events[before:]
    )


async def test_a_pivot_supersedes_the_old_direction_and_plans_for_the_new_one() -> None:
    from mde_compass.engine.ports import ExtractedFact, TurnUnderstanding

    svc = planner_service()
    assert svc.ports is not None
    real = svc.ports.understander

    class Pivoting:
        async def understand(self, ctx, text, **kw):  # type: ignore[no-untyped-def]
            if text.startswith("Actually"):
                return TurnUnderstanding(
                    intent="answer",
                    flags=["pivot"],
                    facts=[
                        ExtractedFact(
                            type="context",
                            statement="It is an appointment app for a clinic",
                            quote="it is for a clinic",
                            topics=["idea"],
                        )
                    ],
                )
            return await real.understand(ctx, text, **kw)

    object.__setattr__(svc.ports, "understander", Pivoting())
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.answer(LONG, card=first)
    async with svc.store.begin("t1") as uow:
        old_ids = {
            f.id
            for f in await uow.facts.list(conv.session_id)
            if f.status == "active" and "idea" in f.topics
        }
    before = len(conv.events)
    await conv.say("Actually, it is for a clinic")
    kinds = [e.type for e in conv.events[before:]]
    assert (
        "notice" in kinds and "question_batch" in kinds
    )  # a notice, then a plan for the new direction
    assert any(e.payload.get("code") == "pivot" for e in conv.events[before:] if e.type == "notice")
    async with svc.store.begin("t1") as uow:
        facts = await uow.facts.list(conv.session_id)
    assert old_ids and all(
        f.status == "superseded" for f in facts if f.id in old_ids
    )  # history kept, not deleted
    assert any(f.status == "active" and "clinic" in f.statement for f in facts)


async def test_a_document_uploaded_mid_interview_adds_facts_with_their_source() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    doc = (
        "Product notes\n\nWe run a tiffin service in Pune. Customers order a monthly meal plan.\n"
        "Delivery must finish before 12:30. We need a rider app and an admin panel.\n"
    )
    res = await svc.ingest_upload(conv.caller, conv.session_id, "notes.txt", doc.encode())
    conv.events.extend(res.events)
    async with svc.store.begin("t1") as uow:
        facts = await uow.facts.list(conv.session_id)
    from_doc = [f for f in facts if f.origin == "document"]
    assert from_doc and all(f.source_ref.startswith("SRC-") and f.claim_id for f in from_doc)
    assert all(f.status == "active" for f in from_doc)


async def test_the_questions_and_answers_list_reads_the_ledger_for_the_history_tab() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    first = conv.last_card()
    assert first is not None
    await conv.answer(LONG, card=first)
    await conv.skip()
    listing = await svc.qa_list(conv.caller, conv.session_id)
    items = listing["items"]
    assert isinstance(items, list) and len(items) >= 2
    one = next(i for i in items if i["card_id"] == first.id)  # type: ignore[index,union-attr]
    assert one["status"] == "answered" and LONG[:30] in one["answer"] and one["facts"]  # type: ignore[index,operator]
    assert one["origin"] == "planner" and one["question"] == first.question  # type: ignore[index]
    assert any(i["status"] == "skipped" for i in items)  # type: ignore[index,union-attr]


async def test_a_scripted_gateway_can_replace_the_model_for_exact_behaviour_tests() -> None:
    """The injection point the scenario fixtures use: hand-written model outputs per call, through the real
    CallRunner, validators and engine. A call nobody scripted fails loudly (an error event), never silently."""
    from mde_compass.calls.testing import ScriptedGateway

    gateway = ScriptedGateway()
    interview = dataclasses.replace(DEFAULT_CONFIG.interview, engine="planner")
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview)
    svc = build_service(memory=True, clock=FakeClock(), compass_config=config, gateway=gateway)
    conv = await Conversation.start(svc)
    result = await conv.say(IDEA)
    assert gateway.calls and gateway.calls[0][0] == "cmp.understand_turn"
    assert any(e.type == "error" for e in result.events)


async def test_i_do_not_understand_rewrites_the_question_once_then_a_second_time_decides() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    card = conv.last_card()
    assert card is not None
    await conv.say("I don't understand")
    again = conv.last_card()
    assert again is not None and again.id == card.id and again.rephrased == 1
    assert again.question != card.question and "[mock] rephrased" in again.question
    assert [o.id for o in again.options] == [
        o.id for o in card.options
    ]  # the choices keep their meaning
    await conv.say("what do you mean")
    third = conv.last_card()
    assert third is not None and third.id == card.id and third.rephrased == 2
    await conv.say("please explain")  # a third request: not pushed, "You decide" assumes visibly
    assert conv.last_card() is not None and conv.last_card().id != card.id  # type: ignore[union-attr]
    assert any(
        e.type == "notice" and e.payload["code"] == "simplified_decided" for e in conv.events
    )
    async with svc.store.begin("t1") as uow:
        qa = {q.card_id: q for q in await uow.qa.list(conv.session_id)}
    assert qa[card.id].status == "you_decide"


async def test_i_do_not_know_simplifies_once_then_you_decide() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    card = conv.last_card()
    assert card is not None
    await conv.say("I don't know")
    simpler = conv.last_card()
    assert simpler is not None and simpler.id == card.id and "[mock] simpler" in simpler.question
    await conv.say("not sure")
    nxt = conv.last_card()
    assert nxt is not None and nxt.id != card.id
    assert any(
        e.type == "notice" and e.payload["code"] == "simplified_decided" for e in conv.events
    )


async def test_help_phrases_do_nothing_new_on_the_legacy_engine() -> None:
    from mde_compass.engine.router import route, route_help

    assert route_help("I don't understand") == "explain" and route_help("pata nahi") == "dont_know"
    assert route_help("समझ नहीं आया") == "explain" and route_help("I run a clinic in Indore") is None
    # the legacy router does not know these kinds at all
    assert route("I don't understand", has_pending_card=True) is None


async def test_option_tap_bypasses_understand_turn() -> None:
    svc = planner_service()
    conv = await Conversation.start(svc)
    await conv.say(IDEA)

    # Advance until we find a card with options
    for _ in range(5):
        card = conv.last_card()
        if card is not None and card.options:
            break
        if card is not None:
            await conv.answer(LONG, card=card)

    card = conv.last_card()
    assert card is not None and card.options

    async with svc.store.begin(conv.caller.tenant_id) as uow:
        calls_before = await uow.call_log.list(conv.session_id)
    understand_before = len([c for c in calls_before if c.spec_id == "cmp.understand_turn"])

    opt = card.recommended_option() or card.options[0]
    await conv.answer(opt.id, card=card)

    async with svc.store.begin(conv.caller.tenant_id) as uow:
        calls_after = await uow.call_log.list(conv.session_id)
        facts = await uow.facts.list(conv.session_id)
    understand_after = len([c for c in calls_after if c.spec_id == "cmp.understand_turn"])

    assert understand_after == understand_before
    tap_facts = [f for f in facts if f.origin == "tap"]
    assert len(tap_facts) >= 1
    assert any(f.qa_card_id == card.id for f in tap_facts)
