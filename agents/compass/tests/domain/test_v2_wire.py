"""Wire and model additions of Compass v2 (ADR 0016): all additive and behavior-neutral.

* ``DomainFact.kind`` / ``refs`` are omitted from the dump while empty, so the hash of every Brief written before
  they existed is unchanged; set, they are part of the content;
* the closing question is a card origin and a planner CALL mode, but it is stored as mode ``replan``;
* ``BatchAnswer`` can say "decide the rest", an item can say "none of these", a batch can carry its progress."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mde_compass.domain import DomainFact, InterviewBlock, brief_hash, stored_mode
from mde_compass.domain.events import BatchAnswer, BatchAnswerItem, BatchProgress, QuestionBatch
from mde_compass.engine.brief_builder import empty_brief
from mde_compass.engine.ports import DecideItem, DecideRequest, PlanContext, PlanRequest
from mde_compass.scenarios.doubles import make_card


def fact(**kw: object) -> DomainFact:
    base: dict[str, object] = {
        "id": "FCT-001",
        "type": "rule",
        "statement": "Orders close at 10 am",
    }
    base.update(kw)
    return DomainFact.model_validate(base)


def test_empty_kind_and_refs_are_not_in_the_dump() -> None:
    dump = fact().model_dump(mode="json")
    assert "kind" not in dump and "refs" not in dump
    assert fact(kind="rule", refs=["FTR-001"]).model_dump(mode="json")["kind"] == "rule"
    assert fact(refs=["FTR-001"]).model_dump(mode="json")["refs"] == ["FTR-001"]


def test_a_stored_fact_without_the_new_fields_loads_and_dumps_the_same() -> None:
    stored = {"id": "FCT-002", "type": "number", "statement": "200 lunch boxes a day"}
    again = DomainFact.model_validate(stored).model_dump(mode="json", exclude_defaults=False)
    assert {k: v for k, v in again.items() if k in ("id", "type", "statement")} == stored
    assert "kind" not in again and "refs" not in again


def test_the_brief_hash_is_unchanged_by_empty_details_and_changes_with_real_ones() -> None:
    brief = empty_brief("ses_1", "guided", "en")
    brief.domain_facts = [fact()]
    plain = brief_hash(brief)
    brief.domain_facts = [fact(kind="")]
    assert brief_hash(brief) == plain
    brief.domain_facts = [fact(kind="rule")]
    assert brief_hash(brief) != plain


def test_the_closing_question_is_a_card_origin_and_is_stored_as_replan() -> None:
    card = make_card("Q-009", origin="closing", type="priority")
    assert card.origin == "closing"
    assert stored_mode("closing") == "replan"
    assert [stored_mode(m) for m in ("initial", "followup", "replan", "deepen", "simplify")] == [
        "initial",
        "followup",
        "replan",
        "deepen",
        "simplify",
    ]


def test_a_plan_request_may_ask_for_the_closing_mode_and_carry_followup_candidates() -> None:
    req = PlanRequest(
        mode="closing", context=PlanContext(), followup_candidates=["audience.segments"]
    )
    assert (
        req.mode == "closing"
        and req.closing is None
        and req.followup_candidates == ["audience.segments"]
    )
    with pytest.raises(ValidationError):
        PlanRequest(mode="finale", context=PlanContext())  # type: ignore[arg-type]


def test_batch_answer_carries_decide_rest_and_the_none_action() -> None:
    ev = BatchAnswer(
        event_id="evt_01",
        batch_id="BAT-001",
        answers=[BatchAnswerItem(card_id="Q-001", action="none")],
        decide_rest=True,
    )
    assert ev.decide_rest and ev.answers[0].action == "none"
    assert BatchAnswer(event_id="evt_02", batch_id="BAT-001", answers=[]).decide_rest is False
    with pytest.raises(ValidationError):
        BatchAnswerItem(card_id="Q-001", action="maybe")  # type: ignore[arg-type]


def test_a_question_batch_may_carry_its_progress_and_older_ones_have_none() -> None:
    old = QuestionBatch(batch_id="BAT-001", mode="initial", groups=[])
    assert old.progress is None
    new = QuestionBatch(
        batch_id="BAT-002",
        mode="replan",
        groups=[],
        progress=BatchProgress(tier="quick", target=7, hard_cap=10, committed=4, closing=False),
    )
    assert QuestionBatch.model_validate(new.model_dump(mode="json")).progress == new.progress


def test_the_interview_block_records_the_tier_numbers_and_the_closing_state() -> None:
    block = InterviewBlock()
    assert (block.target, block.hard_cap, block.tier_set, block.closing) == (0, 0, False, "none")
    assert InterviewBlock(target=7, hard_cap=10, tier_set=True, closing="asked").closing == "asked"
    with pytest.raises(ValidationError):
        InterviewBlock(closing="later")  # type: ignore[arg-type]


def test_the_decide_request_is_bounded_to_twelve_items() -> None:
    item = DecideItem(card_id="Q-001", question="Who uses it?", card_type="choose_one")
    assert len(DecideRequest(items=[item] * 12).items) == 12
    with pytest.raises(ValidationError):
        DecideRequest(items=[item] * 13)
