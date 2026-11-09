"""Interview Memory, Needs Registry and planner vocabulary (build prompt 02): shape and invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mde_compass.config import CompassConfig, InterviewPolicy
from mde_compass.domain import (
    INBOUND_ADAPTER,
    OUTBOUND_ADAPTER,
    AnswerSpec,
    BatchGroup,
    BatchRecord,
    Card,
    CardOption,
    Fact,
    FactEffect,
    NeedStatus,
    PlannedBatch,
    QaRecord,
)
from mde_compass.domain.events import QuestionBatch


def _fact(**kw: object) -> Fact:
    base: dict[str, object] = {
        "id": "FCT-001",
        "type": "rule",
        "statement": "Riders are independent contractors, not staff.",
        "quote": "our riders are freelancers",
        "origin": "stated",
    }
    base.update(kw)
    return Fact.model_validate(base)


def _card(cid: str = "Q-001", **kw: object) -> Card:
    base: dict[str, object] = {
        "id": cid,
        "frame": "planned",
        "slots": [],
        "type": "open_prompt",
        "question": "Do your riders work for you or for themselves?",
        "origin": "planner",
        "need_ids": ["roles.list"],
        "intent": "learn who the riders are",
    }
    base.update(kw)
    return Card.model_validate(base)


@pytest.mark.parametrize("bad", ["fct-001", "FCT-1", "F-001", "FCT-", "1"])
def test_fact_id_shape_is_enforced(bad: str) -> None:
    with pytest.raises(ValidationError):
        _fact(id=bad)


def test_fact_rejects_unknown_fields_and_empty_statement() -> None:
    with pytest.raises(ValidationError):
        _fact(unexpected=1)
    with pytest.raises(ValidationError):
        _fact(statement="")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"origin": "tap", "quote": "Monthly plan"}, True),
        ({"origin": "document", "confidence": 0.9}, True),
        ({"origin": "document", "confidence": 0.7}, True),
        ({"origin": "document", "confidence": 0.69}, False),  # a claim still to confirm (ADR 0016)
        ({"origin": "stated", "confidence": 0.1}, True),  # only documents are held to a confidence
        ({"origin": "assumption", "quote": ""}, False),  # an assumption is never Locked-capable
        ({"unverified_quote": True}, False),  # the quote was not found in its source
        ({"quote": "   "}, False),
        ({"status": "superseded"}, False),
        ({"status": "disputed"}, False),
        ({"status": "not_used"}, False),
    ],
)
def test_only_verified_user_sourced_active_facts_support_locked(
    changes: dict[str, object], expected: bool
) -> None:
    assert _fact(**changes).supports_locked() is expected


def test_fact_effects_default_to_set_with_null_json() -> None:
    effect = FactEffect(slot_id="context.geo")
    assert effect.mode == "set" and effect.value_json == "null"
    assert _fact(effects=[effect.model_dump()]).effects[0].slot_id == "context.geo"


def test_older_cards_still_validate_and_new_fields_default_quietly() -> None:
    legacy = Card.model_validate(
        {
            "id": "Q-009",
            "frame": "business_model",
            "slots": ["idea.business_model"],
            "type": "choose_one",
            "question": "q",
        }
    )
    assert legacy.origin == "frame" and legacy.depth == 0 and legacy.need_ids == []
    assert legacy.batch_id is None and legacy.parent_card_id is None
    assert CardOption(id="A", label="x").description == ""


def test_followup_card_carries_parent_and_depth() -> None:
    card = _card("Q-007", origin="followup", parent_card_id="Q-003", depth=1)
    assert (card.parent_card_id, card.depth, card.origin) == ("Q-003", 1, "followup")
    with pytest.raises(ValidationError):
        _card(depth=-1)


def test_planned_batch_flattens_cards_in_group_order() -> None:
    batch = PlannedBatch(
        batch_id="BAT-001",
        mode="initial",
        groups=[
            BatchGroup(id="G1", title="How it works", cards=[_card("Q-001"), _card("Q-002")]),
            BatchGroup(id="G2", title="Who orders", cards=[_card("Q-003")]),
        ],
    )
    assert [c.id for c in batch.cards()] == ["Q-001", "Q-002", "Q-003"]
    with pytest.raises(ValidationError):
        PlannedBatch(batch_id="BAT-002", mode="bogus")  # type: ignore[arg-type]


def test_qa_record_keeps_the_full_card_and_versions() -> None:
    qa = QaRecord(card_id="Q-001", card=_card(), status="answered", answer="A", input_mode="tap")
    again = QaRecord.model_validate_json(qa.model_dump_json())
    assert again.card.question == qa.card.question and again.version == 1
    with pytest.raises(ValidationError):
        QaRecord(card_id="Q-001", card=_card(), version=0)


def test_answer_spec_needs_a_sufficient_statement() -> None:
    assert (
        AnswerSpec(sufficient="country, city, languages", aspects=["country", "city"]).min_facts
        == 1
    )
    with pytest.raises(ValidationError):
        AnswerSpec(sufficient="")


def test_need_status_bounds() -> None:
    assert NeedStatus(need_id="context.geo", state="partial", impact=5).followups_used == 0
    with pytest.raises(ValidationError):
        NeedStatus(need_id="context.geo", state="missing", impact=6)
    with pytest.raises(ValidationError):
        NeedStatus(need_id="context.geo", state="unknown", impact=3)  # type: ignore[arg-type]


def test_batch_record_defaults_flag_nothing() -> None:
    rec = BatchRecord(id="BAT-001", mode="replan", inputs_hash="abc")
    assert rec.used_fallback is False and rec.repaired is False and rec.accepted == []


def test_new_events_round_trip_through_the_adapters() -> None:
    batch = QuestionBatch(
        batch_id="BAT-001",
        mode="initial",
        groups=[BatchGroup(id="G1", title="How it works", cards=[_card()])],
        total=1,
    )
    out = OUTBOUND_ADAPTER.validate_python(batch.model_dump(mode="json"))
    assert isinstance(out, QuestionBatch) and out.groups[0].cards[0].need_ids == ["roles.list"]

    answer = INBOUND_ADAPTER.validate_python(
        {
            "type": "batch_answer",
            "event_id": "e1",
            "batch_id": "BAT-001",
            "answers": [{"card_id": "Q-001", "value": "A"}, {"card_id": "Q-002", "action": "skip"}],
        }
    )
    assert answer.type == "batch_answer" and answer.answers[1].action == "skip"  # type: ignore[union-attr]
    edit = INBOUND_ADAPTER.validate_python(
        {"type": "answer_edit", "event_id": "e2", "card_id": "Q-001", "value": "B"}
    )
    assert edit.type == "answer_edit"
    for name in ("ask_more", "prepare_questions"):
        cmd = INBOUND_ADAPTER.validate_python(
            {"type": "command", "event_id": f"c-{name}", "name": name}
        )
        assert cmd.name == name  # type: ignore[union-attr]


def test_batch_answer_is_bounded() -> None:
    too_many = [{"card_id": f"Q-{i:03d}"} for i in range(13)]
    with pytest.raises(ValidationError):
        INBOUND_ADAPTER.validate_python(
            {"type": "batch_answer", "event_id": "e3", "batch_id": "BAT-001", "answers": too_many}
        )


def test_interview_policy_budgets_and_defaults() -> None:
    policy = CompassConfig().interview
    assert (
        policy.engine == "planner"
    )  # switched on in phase 7; "legacy" stays as a rollback until phase 8
    assert [policy.budget_max(b) for b in ("quick", "standard", "thorough")] == [5, 12, 25]
    assert InterviewPolicy(engine="legacy").engine == "legacy"
    assert 0 < policy.followup_max_fraction < 1 and policy.followup_max_depth == 2
    assert (
        CompassConfig().context.fact_digest * 4 < 20_000
    )  # under the gateway's untrusted-input cap


def test_fact_records_which_need_aspects_it_covers() -> None:
    fact = _fact(aspects=["audience.segments:who", "audience.segments:goal"])
    assert fact.aspects == ["audience.segments:who", "audience.segments:goal"]
    assert _fact().aspects == []
    complete = _fact(
        origin="tap", aspects=["idea.business_model:*"]
    )  # a tapped option answers fully
    assert complete.aspects == ["idea.business_model:*"]
