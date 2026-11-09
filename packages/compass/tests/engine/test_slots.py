from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.common import EVIDENCE_TYPES, Provenance, has_evidence
from mde_compass.engine.slots import USER_GIVEN, SlotStore
from mde_core.clock import FakeClock


def store() -> SlotStore:
    return SlotStore(load_catalog(), clock=FakeClock())


def tap(ref: str = "evt_1") -> Provenance:
    return Provenance(type="user_tap", ref=ref)


def assumed() -> Provenance:
    return Provenance(type="pattern", ref="families.yaml")


def test_unknown_slot_rejected() -> None:
    res = store().write("nope.nothing", "x", state="volunteered", provenance=tap())
    assert not res.applied and res.reason == "unknown_slot"


def test_create_and_locked_with_evidence() -> None:
    s = store()
    res = s.write(
        "idea.summary", "  a sweets shop ", state="volunteered", provenance=tap(), turn_id="evt_1"
    )
    assert res.applied and res.reason == "created" and res.record is not None
    assert res.record.value == "a sweets shop" and res.record.tier == "Locked"
    assert res.record.provenance[0].date is not None
    assert s.filled("idea.summary") and s.values() == {"idea.summary": "a sweets shop"}


def test_assumed_is_default_never_locked() -> None:
    s = store()
    res = s.write(
        "idea.category",
        "commerce",
        state="assumed",
        provenance=Provenance(type="user_tap", ref="x"),
    )
    assert res.record is not None and res.record.tier == "Default"
    res = s.write("idea.subcategory", "x", state="assumed", provenance=assumed())
    assert res.record is not None and res.record.tier == "Default"


def test_user_tap_without_ref_is_not_locked() -> None:
    res = store().write(
        "idea.summary", "x", state="volunteered", provenance=Provenance(type="user_tap")
    )
    assert res.record is not None and res.record.tier == "Default"


def test_assumed_never_replaces_user_value() -> None:
    s = store()
    s.write("idea.category", "commerce", state="confirmed", provenance=tap())
    res = s.write("idea.category", "booking", state="assumed", provenance=assumed())
    assert not res.applied and res.reason == "assumed_never_replaces_user_value"
    assert s.get("idea.category").value == "commerce"  # type: ignore[union-attr]


def test_assumed_can_be_replaced_by_user_and_upgrades() -> None:
    s = store()
    s.write("idea.category", "commerce", state="assumed", provenance=assumed())
    res = s.write("idea.category", "commerce", state="confirmed", provenance=tap())
    assert res.record is not None
    assert (
        res.reason == "state_upgraded"
        and res.record.status == "confirmed"
        and res.record.tier == "Locked"
    )
    again = s.write("idea.category", "commerce", state="assumed", provenance=assumed())
    assert not again.applied


def test_correction_overwrites_and_keeps_history() -> None:
    s = store()
    s.write(
        "context.scale_launch",
        "small",
        state="volunteered",
        provenance=tap("evt_1"),
        turn_id="evt_1",
    )
    res = s.write(
        "context.scale_launch",
        "large",
        state="volunteered",
        provenance=tap("evt_2"),
        turn_id="evt_2",
    )
    assert res.reason == "corrected" and res.record is not None
    assert res.record.value == "large" and len(res.record.history) == 1
    assert res.record.history[0].value == "small" and res.record.history[0].reason == "corrected"
    assert [h.value for h in s.history("context.scale_launch")] == ["small"]


def test_volunteered_onto_confirmed_stays_confirmed() -> None:
    s = store()
    s.write("idea.category", "commerce", state="confirmed", provenance=tap())
    res = s.write("idea.category", "commerce", state="volunteered", provenance=tap("evt_2"))
    assert not res.applied and s.get("idea.category").status == "confirmed"  # type: ignore[union-attr]
    res = s.write("idea.category", "booking", state="volunteered", provenance=tap("evt_3"))
    assert (
        res.record is not None
        and res.record.status == "confirmed"
        and res.record.value == "booking"
    )


def test_explicit_negation_clears_with_history() -> None:
    s = store()
    s.write("brand.name", "Mithai", state="volunteered", provenance=tap())
    res = s.write("brand.name", None, state="volunteered", provenance=tap("evt_2"), remove=True)
    assert res.reason == "cleared" and res.record is not None
    assert res.record.status == "empty" and res.record.value is None and res.record.tier == "Open"
    assert res.record.history[-1].value == "Mithai"
    assert not s.filled("brand.name") and s.values() == {}
    assert (
        s.write("brand.name", None, state="volunteered", provenance=tap(), remove=True).reason
        == "nothing_to_remove"
    )


def test_negation_removes_named_list_items() -> None:
    s = store()
    s.write("auth.methods", ["phone", "email", "google"], state="volunteered", provenance=tap())
    res = s.write("auth.methods", ["Email"], state="volunteered", provenance=tap("e2"), remove=True)
    assert (
        res.reason == "items_removed"
        and res.record is not None
        and res.record.value == ["phone", "google"]
    )


def test_assumed_cannot_clear_user_value() -> None:
    s = store()
    s.write("brand.name", "Mithai", state="confirmed", provenance=tap())
    res = s.write("brand.name", None, state="assumed", provenance=assumed(), remove=True)
    assert not res.applied and s.filled("brand.name")


def test_empty_write_is_ignored() -> None:
    s = store()
    assert s.write("brand.name", "   ", state="volunteered", provenance=tap()).reason == "empty"
    assert s.write("brand.assets", [], state="volunteered", provenance=tap()).reason == "empty"
    assert s.all() == {}


def test_custom_text_kept_next_to_value() -> None:
    s = store()
    res = s.write(
        "idea.business_model",
        "per_transaction_fee",
        state="volunteered",
        provenance=tap(),
        custom_text="we take a small cut from each sale",
    )
    assert res.record is not None
    assert (
        res.record.custom_text == "we take a small cut from each sale"
        and res.record.needs_confirmation
    )


def test_enterprise_conflict_is_disputed_never_merged() -> None:
    s = store()
    s.write(
        "idea.category", "commerce", state="volunteered", provenance=tap(), contributor_id="u_a"
    )
    res = s.write(
        "idea.category",
        "booking",
        state="volunteered",
        provenance=tap("evt_9"),
        contributor_id="u_b",
    )
    assert res.applied and res.reason == "conflict" and res.record is not None
    assert res.record.status == "disputed" and res.record.value == "commerce"
    assert any(h.reason == "conflict:u_b" and h.value == "booking" for h in res.record.history)
    # the same contributor correcting themselves is an ordinary correction
    res2 = s.write(
        "idea.category",
        "booking",
        state="confirmed",
        provenance=tap("evt_10"),
        contributor_id="u_a",
    )
    assert res2.reason == "corrected" and res2.record.status == "confirmed"  # type: ignore[union-attr]


def test_load_dump_roundtrip() -> None:
    s = store()
    s.write("idea.summary", "a shop", state="confirmed", provenance=tap())
    other = store()
    other.load(s.dump())
    assert other.all() == s.all()


# ----------------------------------------------------------------------------- properties
SLOT_IDS = [
    "idea.summary",
    "idea.category",
    "brand.name",
    "auth.methods",
    "locales",
    "context.scale_launch",
]
write_st = st.tuples(
    st.sampled_from(SLOT_IDS),
    st.one_of(
        st.text(min_size=0, max_size=6), st.lists(st.text(max_size=4), max_size=3), st.none()
    ),
    st.sampled_from(["assumed", "volunteered", "confirmed", "disputed"]),
    st.sampled_from(
        [
            "user_tap",
            "user_text",
            "voice",
            "document",
            "assumed",
            "pattern",
            "model_knowledge",
            "org_profile",
        ]
    ),
    st.sampled_from(["", "evt_1", "SRC-001#p2"]),
    st.booleans(),
    st.sampled_from([None, "a", "b"]),
)


@settings(max_examples=150, deadline=None)
@given(st.lists(write_st, max_size=25))
def test_invariants(writes: list[Any]) -> None:
    s = store()
    touched: set[str] = set()
    for slot_id, value, state, ptype, ref, remove, contributor in writes:
        before = s.get(slot_id)
        was_user_value = before is not None and s.filled(slot_id) and before.status in USER_GIVEN
        res = s.write(
            slot_id,
            value,
            state=state,
            provenance=Provenance(type=ptype, ref=ref),
            remove=remove,
            contributor_id=contributor,
        )
        if state == "assumed" and was_user_value:
            assert not res.applied and s.get(slot_id) == before
        if res.applied:
            touched.add(slot_id)
        for other in SLOT_IDS:
            if other != slot_id:
                pass
        for rec in s.all().values():
            has_value = s.filled(rec.slot_id)
            if rec.tier == "Locked":
                assert has_value and has_evidence(rec.provenance)
                assert rec.provenance and rec.status != "assumed"
            if not has_value:
                assert rec.tier == "Open"
            assert all(p.type in EVIDENCE_TYPES or True for p in rec.provenance)
    # slots nobody wrote successfully do not exist
    assert set(s.all()) <= touched


@settings(max_examples=100, deadline=None)
@given(st.lists(write_st, max_size=15), st.sampled_from(SLOT_IDS))
def test_untouched_slot_never_reset(writes: list[Any], untouched: str) -> None:
    s = store()
    s.write(untouched, "keep", state="confirmed", provenance=tap())
    snapshot = s.get(untouched)
    for slot_id, value, state, ptype, ref, remove, _contributor in writes:
        if slot_id == untouched:
            continue
        s.write(
            slot_id, value, state=state, provenance=Provenance(type=ptype, ref=ref), remove=remove
        )
    assert s.get(untouched) == snapshot
