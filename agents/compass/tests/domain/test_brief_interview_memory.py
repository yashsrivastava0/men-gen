"""The Brief after the Interview Memory (ADR 0015): domain_facts, meta.interview, and hash stability.

The fixture was produced by the code BEFORE these fields existed. A Brief written then must keep its hash and
its section hashes, or every stored approval would silently go stale."""

from __future__ import annotations

import json
from pathlib import Path

from mde_compass.domain import (
    OPTIONAL_SECTIONS,
    READ_ONLY_SECTIONS,
    SECTION_KEYS,
    Brief,
    DomainFact,
    InterviewBlock,
    brief_hash,
    changed_sections,
    section_hashes,
)
from mde_compass.domain.brief import ID_FIELD_BY_SECTION

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/brief_before_interview_memory.json").read_text(
        encoding="utf-8"
    )
)


def _brief() -> Brief:
    return Brief.model_validate(FIXTURE["brief"])


def _fact(**kw: object) -> DomainFact:
    base: dict[str, object] = {
        "id": "FCT-001",
        "type": "rule",
        "statement": "Riders are independent contractors.",
        "topics": ["riders"],
        "consumers": ["blueprint"],
        "quote": "our riders are freelancers",
    }
    base.update(kw)
    return DomainFact.model_validate(base)


def test_a_brief_written_before_the_memory_keeps_its_hash_and_section_hashes() -> None:
    brief = _brief()
    assert brief_hash(brief) == FIXTURE["brief_hash"]
    assert section_hashes(brief) == FIXTURE["section_hashes"]
    assert len(FIXTURE["section_hashes"]) == len(SECTION_KEYS) - len(OPTIONAL_SECTIONS)


def test_facts_join_the_hashes_only_once_there_are_some() -> None:
    before, after = _brief(), _brief()
    after.domain_facts.append(_fact())
    assert brief_hash(before) != brief_hash(after)
    assert "domain_facts" in section_hashes(after) and "domain_facts" not in section_hashes(before)
    assert changed_sections(section_hashes(before), section_hashes(after)) == ["domain_facts"]
    assert (
        changed_sections(section_hashes(before), section_hashes(_brief())) == []
    )  # absent == absent


def test_a_changed_fact_changes_only_its_section() -> None:
    a, b = _brief(), _brief()
    a.domain_facts.append(_fact())
    b.domain_facts.append(_fact(statement="Riders are staff."))
    assert changed_sections(section_hashes(a), section_hashes(b)) == ["domain_facts"]


def test_the_interview_block_never_moves_a_hash() -> None:
    plain, audited = _brief(), _brief()
    audited.meta.interview = InterviewBlock(
        questions_asked=9, followups=2, facts_active=14, fallback_count=1, question_ids=["Q-001"]
    )
    assert brief_hash(plain) == brief_hash(audited) == FIXTURE["brief_hash"]
    again = audited.model_copy(deep=True)
    again.meta.interview = InterviewBlock(questions_asked=30, coverage=0.5)
    assert brief_hash(again) == FIXTURE["brief_hash"]


def test_the_interview_budget_is_content_only_once_chosen() -> None:
    brief = _brief()
    assert brief.meta.plan is not None and brief.meta.plan.interview_budget is None
    chosen = brief.model_copy(deep=True)
    assert chosen.meta.plan is not None
    chosen.meta.plan.interview_budget = "thorough"
    assert brief_hash(chosen) != brief_hash(
        brief
    )  # a deliberate choice is part of what was approved


def test_domain_facts_are_read_only_and_keep_their_ids_out_of_the_brief_ledger() -> None:
    assert "domain_facts" in SECTION_KEYS and "domain_facts" in READ_ONLY_SECTIONS
    assert "domain_facts" not in ID_FIELD_BY_SECTION  # FCT ids belong to the fact ledger
    brief = _brief()
    brief.domain_facts.append(_fact())
    assert Brief.model_validate_json(brief.model_dump_json()).domain_facts[0].id == "FCT-001"


def test_a_patch_cannot_edit_the_details_we_captured() -> None:
    from mde_compass.catalog.loader import load_catalog
    from mde_compass.domain import PatchOp
    from mde_compass.engine.patch import apply_ops
    from mde_core.clock import FakeClock

    brief = _brief()
    brief.domain_facts.append(_fact())
    for op in (
        PatchOp(op="set", path="domain_facts.FCT-001.statement", value="changed"),
        PatchOp(op="remove", path="domain_facts.FCT-001"),
        PatchOp(op="add", path="domain_facts", value={"id": "FCT-002"}),
    ):
        result = apply_ops(brief, [op], load_catalog(), now=FakeClock().now())
        assert any("read from your answers" in e for e in result.errors), op
        assert result.brief.domain_facts == brief.domain_facts and not result.ops
