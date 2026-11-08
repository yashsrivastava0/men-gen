"""Assumptions: propose, then approve; stable ids; silence never approves."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import BriefItem
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.assumptions import (
    THEME_BY_GROUP,
    THEME_ORDER,
    accept_all_low,
    assumption_from_slot,
    decide,
    review,
    sync_assumptions,
)

CATALOG = load_catalog()
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)


def assumed(
    slot_id: str, value: object, basis: str = "pattern", confidence: float = 0.6
) -> SlotRecord:
    return SlotRecord.model_validate(
        {
            "slot_id": slot_id,
            "value": value,
            "status": "assumed",
            "confidence": confidence,
            "tier": "Default",
            "provenance": [{"type": basis}],
        }
    )


def given_by_user(slot_id: str, value: object) -> SlotRecord:
    return SlotRecord.model_validate(
        {
            "slot_id": slot_id,
            "value": value,
            "status": "confirmed",
            "confidence": 1.0,
            "tier": "Locked",
            "provenance": [{"type": "user_tap", "ref": "evt_1"}],
        }
    )


def test_every_catalog_group_has_a_theme() -> None:
    assert {s.group for s in CATALOG.slots.values()} <= set(THEME_BY_GROUP)
    assert set(THEME_BY_GROUP.values()) <= set(THEME_ORDER)


def test_assumption_from_slot_fields() -> None:
    brief = Brief()
    a = assumption_from_slot(
        CATALOG.slots["context.scale_launch"],
        assumed("context.scale_launch", "pilot_small"),
        brief.ids,
        language="en",
        catalog=CATALOG,
    )
    assert a.id == "ASM-001"
    assert a.impact == "low"
    assert a.theme == "Business"
    assert a.basis == "pattern"
    assert a.decision == "proposed"
    assert a.status == "assumed"
    assert a.tier == "Default"  # an assumption is never Locked
    assert "pilot_small" in a.claim
    assert a.what_changes
    assert a.slot_id == "context.scale_launch"
    hi = assumption_from_slot(
        CATALOG.slots["context.scale_launch"],
        assumed("context.scale_launch", "pilot_small"),
        brief.ids,
        language="hi",
        catalog=CATALOG,
    )
    assert hi.id == "ASM-002"
    assert any("ऀ" <= c <= "ॿ" for c in hi.what_changes)


def test_high_impact_from_catalog_impact_or_risk() -> None:
    brief = Brief()
    high = assumption_from_slot(
        CATALOG.slots["idea.business_model"],
        assumed("idea.business_model", "subscription", "model_knowledge"),
        brief.ids,
        language="en",
        catalog=CATALOG,
    )
    risky = assumption_from_slot(
        CATALOG.slots["auth.methods"],
        assumed("auth.methods", ["phone_otp"]),
        brief.ids,
        language="en",
        catalog=CATALOG,
    )
    low = assumption_from_slot(
        CATALOG.slots["devices.profile"],
        assumed("devices.profile", "phones"),
        brief.ids,
        language="en",
        catalog=CATALOG,
    )
    assert (high.impact, risky.impact, low.impact) == ("high", "high", "low")
    assert high.basis == "model_knowledge"
    assert risky.theme == "Access"


def test_sync_adds_updates_keeps_ids_and_drops_user_given() -> None:
    store = {
        "context.scale_launch": assumed("context.scale_launch", "pilot_small"),
        "devices.profile": assumed("devices.profile", "phones"),
        "idea.business_model": assumed("idea.business_model", "subscription"),
    }
    brief = sync_assumptions(Brief(), store, CATALOG)
    ids = {a.slot_id: a.id for a in brief.assumptions}
    assert len(ids) == 3
    # again with nothing changed: same ids, no duplicates, input untouched
    again = sync_assumptions(brief, store, CATALOG)
    assert {a.slot_id: a.id for a in again.assumptions} == ids
    assert len(again.assumptions) == 3
    # the user answers one: the proposed assumption is dropped and its id is tombstoned, never reused
    store["devices.profile"] = given_by_user("devices.profile", "phones")
    after = sync_assumptions(again, store, CATALOG)
    assert "devices.profile" not in {a.slot_id for a in after.assumptions}
    assert ids["devices.profile"] in after.ids.tombstones
    store["scope.must_not"] = assumed("scope.must_not", ["nothing"])
    newer = sync_assumptions(after, store, CATALOG)
    assert max(a.id for a in newer.assumptions) > max(ids.values())


def test_sync_keeps_decisions_unless_the_claim_changes() -> None:
    store = {"context.scale_launch": assumed("context.scale_launch", "pilot_small")}
    brief = sync_assumptions(Brief(), store, CATALOG)
    brief, _ = decide(brief, "ASM-001", "accept", turn_id="evt_2", now=NOW, catalog=CATALOG)
    same = sync_assumptions(brief, store, CATALOG)
    assert same.assumptions[0].decision == "accepted"
    store["context.scale_launch"] = assumed("context.scale_launch", "wide_launch")
    changed = sync_assumptions(same, store, CATALOG)
    assert changed.assumptions[0].id == "ASM-001"
    assert changed.assumptions[0].decision == "proposed"  # a new claim needs a new decision


def test_sync_keeps_a_decided_assumption_when_the_slot_becomes_user_given() -> None:
    store = {"context.scale_launch": assumed("context.scale_launch", "pilot_small")}
    brief, _ = decide(
        sync_assumptions(Brief(), store, CATALOG),
        "ASM-001",
        "change",
        "single_city",
        turn_id="evt_3",
        now=NOW,
        catalog=CATALOG,
    )
    store["context.scale_launch"] = given_by_user("context.scale_launch", "single_city")
    kept = sync_assumptions(brief, store, CATALOG)
    assert [a.decision for a in kept.assumptions] == ["changed"]


def test_review_groups_by_theme_and_lists_ids() -> None:
    store = {
        "context.scale_launch": assumed("context.scale_launch", "pilot_small"),
        "devices.profile": assumed("devices.profile", "phones"),
        "idea.business_model": assumed("idea.business_model", "subscription"),
        "auth.methods": assumed("auth.methods", ["phone_otp"]),
    }
    brief = sync_assumptions(Brief(), store, CATALOG)
    rev = review(brief)
    assert list(rev.groups) == ["Business", "Look", "Access"]  # theme order, empty themes absent
    by = {a.slot_id: a.id for a in brief.assumptions}
    assert set(rev.unreviewed_high_ids) == {by["idea.business_model"], by["auth.methods"]}
    assert set(rev.low_impact_ids) == {by["context.scale_launch"], by["devices.profile"]}


def test_decide_accept_and_change_build_user_evidence_ops() -> None:
    brief = Brief()
    brief.idea["business_model"] = BriefItem(value="subscription", status="assumed", tier="Default")
    store = {"idea.business_model": assumed("idea.business_model", "subscription")}
    brief = sync_assumptions(brief, store, CATALOG)
    out, ops = decide(brief, "ASM-001", "accept", turn_id="evt_9", now=NOW, catalog=CATALOG)
    assert out.assumptions[0].decision == "accepted"
    assert [(o.op, o.path, o.value) for o in ops] == [
        ("set", "idea.business_model", "subscription")
    ]
    assert ops[0].provenance[0].type == "user_tap"
    assert ops[0].provenance[0].ref == "evt_9"
    assert brief.assumptions[0].decision == "proposed"  # the input is not mutated
    out2, ops2 = decide(
        brief,
        "ASM-001",
        "change",
        "per_transaction_fee",
        turn_id="evt_10",
        now=NOW,
        catalog=CATALOG,
    )
    assert out2.assumptions[0].decision == "changed"
    assert ops2[0].value == "per_transaction_fee"
    assert ops2[0].old_value == "subscription"
    assert out2.assumptions[0].history  # the previous decision is kept
    with pytest.raises(KeyError):
        decide(brief, "ASM-099", "accept", turn_id="evt_1", now=NOW, catalog=CATALOG)


def test_accept_all_low_never_touches_high_impact() -> None:
    store = {
        "context.scale_launch": assumed("context.scale_launch", "pilot_small"),
        "idea.business_model": assumed("idea.business_model", "subscription"),
    }
    brief = accept_all_low(sync_assumptions(Brief(), store, CATALOG), turn_id="evt_4", now=NOW)
    decisions = {a.slot_id: a.decision for a in brief.assumptions}
    assert decisions == {"context.scale_launch": "accepted", "idea.business_model": "proposed"}
    assert review(brief).unreviewed_high_ids  # still blocks the gate


@given(
    st.lists(
        st.sampled_from([s for s, d in CATALOG.slots.items() if d.level != "generated"]),
        unique=True,
        max_size=20,
    )
)
def test_silence_never_approves_and_sync_never_decides(slot_ids: list[str]) -> None:
    store = {s: assumed(s, "x") for s in slot_ids}
    brief = sync_assumptions(Brief(), store, CATALOG)
    assert all(a.decision == "proposed" for a in brief.assumptions)
    assert all(a.tier != "Locked" for a in brief.assumptions)
    again = sync_assumptions(brief, store, CATALOG)
    assert [a.id for a in again.assumptions] == [a.id for a in brief.assumptions]


def test_an_assumption_whose_slot_lost_its_value_is_tombstoned_not_left_behind() -> None:
    store = {"context.scale_launch": assumed("context.scale_launch", "pilot_small")}
    brief = sync_assumptions(Brief(), store, CATALOG)
    store["context.scale_launch"] = assumed("context.scale_launch", None)
    after = sync_assumptions(brief, store, CATALOG)
    assert after.assumptions == []
    assert "ASM-001" in after.ids.tombstones


def test_assumptions_of_slots_outside_the_store_snapshot_are_left_alone() -> None:
    store = {"context.scale_launch": assumed("context.scale_launch", "pilot_small")}
    brief = sync_assumptions(Brief(), store, CATALOG)
    assert len(sync_assumptions(brief, {}, CATALOG).assumptions) == 1
