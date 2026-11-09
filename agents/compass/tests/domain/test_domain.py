"""Contract tests for the Compass domain: hashing, tier rules, ids, events, research validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mde_compass.domain import (
    INBOUND_ADAPTER,
    OUTBOUND_ADAPTER,
    SECTION_KEYS,
    AppRecord,
    Brief,
    BriefItem,
    IdLedger,
    ItemMeta,
    Provenance,
    ResearchRequest,
    brief_hash,
    changed_sections,
    max_tier,
    section_hashes,
    tier_errors,
)


def _brief() -> Brief:
    b = Brief()
    b.idea["summary"] = BriefItem(
        value="A food delivery app for Pune",
        status="confirmed",
        tier="Locked",
        provenance=[Provenance(type="user_text", ref="evt_1", quote="food delivery in Pune")],
        confidence=0.9,
    )
    return b


def test_hash_is_stable_and_ignores_bookkeeping() -> None:
    a, b = _brief(), _brief()
    b.version = 7
    b.ids.allocate("APP")
    assert brief_hash(a) == brief_hash(b)
    assert section_hashes(a) == section_hashes(b)


def test_only_the_changed_section_hash_moves() -> None:
    before = _brief()
    after = _brief()
    after.apps.append(AppRecord(id="APP-001", name="Customer app"))
    assert brief_hash(before) != brief_hash(after)
    assert changed_sections(section_hashes(before), section_hashes(after)) == ["apps"]
    assert set(section_hashes(before)) == set(SECTION_KEYS)


def test_locked_needs_evidence_with_a_reference() -> None:
    assert max_tier(has_value=False, provenance=[]) == "Open"
    assert max_tier(has_value=True, provenance=[Provenance(type="assumed", ref="x")]) == "Default"
    assert max_tier(has_value=True, provenance=[Provenance(type="user_tap")]) == "Default"  # no ref
    assert (
        max_tier(has_value=True, provenance=[Provenance(type="user_tap", ref="evt_2")]) == "Locked"
    )
    bad = ItemMeta(tier="Locked", provenance=[Provenance(type="pattern", ref="families.yaml")])
    assert tier_errors(bad, has_value=True)
    ok = ItemMeta(tier="Locked", provenance=[Provenance(type="document", ref="SRC-001 p3")])
    assert not tier_errors(ok, has_value=True)


def test_ids_are_never_reused() -> None:
    ids = IdLedger()
    first = ids.allocate("FTR")
    ids.tombstone(first)
    second = ids.allocate("FTR")
    assert (first, second) == ("FTR-001", "FTR-002") and ids.is_deleted(first)
    ids.alias("FTR-002", "FTR-009")
    assert ids.resolve("FTR-002") == "FTR-009"
    with pytest.raises(ValueError):
        ids.allocate("ZZZ")
    ids.note_existing("FTR-040")
    assert ids.allocate("FTR") == "FTR-041"


def test_inbound_union_parses_and_rejects_bad_input() -> None:
    ev = INBOUND_ADAPTER.validate_python(
        {"type": "card_answer", "event_id": "evt_a1", "card_id": "Q-001", "value": "A"}
    )
    assert ev.type == "card_answer"
    with pytest.raises(ValidationError):
        INBOUND_ADAPTER.validate_python(
            {"type": "user_message", "event_id": "bad id!", "text": "hi"}
        )
    with pytest.raises(ValidationError):
        INBOUND_ADAPTER.validate_python(
            {"type": "user_message", "event_id": "evt_a2", "text": "hi", "surprise": 1}
        )
    gate = INBOUND_ADAPTER.validate_python(
        {"type": "gate_decision", "event_id": "evt_a3", "decision": "approve", "version": 3}
    )
    assert gate.type == "gate_decision" and gate.section_hashes == {}


def test_outbound_union_round_trips() -> None:
    ev = OUTBOUND_ADAPTER.validate_python(
        {"type": "notice", "code": "picked_up", "text": "I picked up: Pune"}
    )
    assert OUTBOUND_ADAPTER.dump_python(ev, mode="json")["type"] == "notice"


def test_research_request_must_be_specific() -> None:
    good = dict(
        id="RR-001",
        topic="Typical features of food delivery apps",
        question="Which features are must-have?",
        decision_unlocked="MVP boundary",
        entity="food delivery apps",
        geography="India, Pune",
        terms=["food delivery"],
    )
    assert ResearchRequest.model_validate(good).depth == "standard"
    for missing in ("geography", "entity", "terms"):
        broken = {**good, missing: [] if missing == "terms" else " "}
        with pytest.raises(ValidationError):
            ResearchRequest.model_validate(broken)
