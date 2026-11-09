from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import Catalog, load_catalog
from mde_compass.domain.interview import Fact
from mde_compass.engine.fact_projector import (
    ProjectionResult,
    fact_refs,
    facts_by_slot,
    project,
    provenance_for,
    reproject,
    verified_fact_ids,
)
from mde_compass.engine.facts import supersede
from mde_compass.engine.slots import SlotStore
from mde_core.clock import FakeClock

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
CATALOG: Catalog = load_catalog()


def store() -> SlotStore:
    return SlotStore(CATALOG, clock=FakeClock())


def eff(slot: str, value: object, mode: str = "set") -> dict[str, object]:
    return {"slot_id": slot, "value_json": json.dumps(value), "mode": mode}


def mk(n: int, effects: list[dict[str, object]], **kw: object) -> Fact:
    base: dict[str, object] = {
        "id": f"FCT-{n:03d}",
        "type": "requirement",
        "statement": f"statement {n}",
        "quote": f"quote {n}",
        "origin": "stated",
        "created_at": NOW + timedelta(seconds=n),
        "effects": effects,
    }
    base.update(kw)
    return Fact.model_validate(base)


def run(fact: Fact, slots: SlotStore) -> ProjectionResult:
    return project(fact, slots, CATALOG, turn_id="evt_1", now=NOW)


# ----------------------------------------------------------------------------------------- provenance
def test_stated_fact_writes_a_locked_volunteered_slot_with_fact_ref() -> None:
    s = store()
    res = run(mk(1, [eff("idea.summary", "a tiffin service")], quote="a tiffin service"), s)
    assert res.written == ["idea.summary"] and not res.rejected
    rec = s.get("idea.summary")
    assert rec is not None and rec.value == "a tiffin service"
    assert rec.status == "volunteered" and rec.tier == "Locked"
    prov = rec.provenance[0]
    assert (prov.type, prov.ref, prov.quote, prov.date) == (
        "user_text",
        "FCT-001",
        "a tiffin service",
        NOW,
    )


def test_voice_tap_document_assumption_types() -> None:
    f = mk(1, [eff("idea.summary", "x")])
    assert provenance_for(f, now=NOW, input_mode="voice").type == "voice"
    assert provenance_for(f, now=NOW).type == "user_text"
    assert provenance_for(mk(1, [], origin="tap"), now=NOW).type == "user_tap"
    assert provenance_for(mk(1, [], origin="document"), now=NOW).type == "document"
    a = provenance_for(mk(1, [], origin="assumption", quote=""), now=NOW)
    assert a.type == "assumed" and a.quote == "" and a.ref == "FCT-001"
    # voice only changes a stated fact
    assert provenance_for(mk(1, [], origin="tap"), now=NOW, input_mode="voice").type == "user_tap"


def test_quote_is_cut_at_300_chars() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "x")], quote="q" * 500), s)
    rec = s.get("idea.summary")
    assert rec is not None and len(rec.provenance[0].quote) == 300


def test_tap_fact_confirms_the_slot() -> None:
    s = store()
    run(mk(1, [eff("context.scale_launch", "single_city")], origin="tap", quote="One city"), s)
    rec = s.get("context.scale_launch")
    assert rec is not None and rec.status == "confirmed" and rec.tier == "Locked"
    assert rec.provenance[0].type == "user_tap"


def test_unverified_quote_is_default_never_locked() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "x")], unverified_quote=True), s)
    rec = s.get("idea.summary")
    assert rec is not None and rec.provenance[0].type == "model_knowledge"
    assert rec.tier == "Default"
    assert rec.provenance[0].ref == "FCT-001" and not rec.provenance[0].is_evidence()


def test_user_fact_without_a_quote_is_default() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "x")], quote=""), s)
    rec = s.get("idea.summary")
    assert rec is not None and rec.tier == "Default"


def test_assumption_fact_is_assumed_default_and_never_replaces_a_user_value() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "mine")]), s)
    res = run(mk(2, [eff("idea.summary", "guess")], origin="assumption", quote=""), s)
    assert res.written == [] and res.rejected[0].reason == "assumed_never_replaces_user_value"
    rec = s.get("idea.summary")
    assert rec is not None and rec.value == "mine" and rec.tier == "Locked"
    s2 = store()
    run(mk(3, [eff("idea.summary", "guess")], origin="assumption", quote=""), s2)
    rec2 = s2.get("idea.summary")
    assert rec2 is not None and rec2.status == "assumed" and rec2.tier == "Default"


def test_inactive_fact_does_not_project() -> None:
    s = store()
    res = run(mk(1, [eff("idea.summary", "x")], status="superseded"), s)
    assert res.written == [] and res.rejected[0].reason == "fact_not_active"
    assert s.get("idea.summary") is None


# --------------------------------------------------------------------------------------- bad values
def test_bad_values_are_returned_not_raised() -> None:
    s = store()
    bad = Fact.model_validate(
        {
            "id": "FCT-009",
            "type": "rule",
            "statement": "s",
            "quote": "qqq",
            "effects": [
                {"slot_id": "idea.summary", "value_json": "{not json"},
                {"slot_id": "idea.summary", "value_json": "NaN"},
                {"slot_id": "idea.summary", "value_json": "null"},
                {"slot_id": "idea.summary", "value_json": '""'},
                {"slot_id": "no.such.slot", "value_json": '"x"'},
                {"slot_id": "context.scale_launch", "value_json": '"galaxy_wide"'},
                {"slot_id": "audience.segments", "value_json": "[1, 2]"},
                {"slot_id": "idea.summary", "value_json": '"' + "z" * 21_000 + '"'},
                {"slot_id": "idea.summary", "value_json": '"x"', "mode": "add"},
            ],
        }
    )
    res = run(bad, s)
    assert res.written == []
    assert [r.reason for r in res.rejected] == [
        "invalid_json",
        "invalid_json",
        "empty_value",
        "empty_value",
        "unknown_slot",
        "value_not_valid_for_slot",
        "value_not_valid_for_slot",
        "value_too_long",
        "add_needs_list_slot",
    ]
    assert all(r.fact_id == "FCT-009" for r in res.rejected)
    assert s.all() == {}


def test_deeply_nested_json_is_rejected_not_crashed() -> None:
    s = store()
    nested = "[" * 5000 + "]" * 5000
    res = run(mk(1, [{"slot_id": "audience.segments", "value_json": nested}]), s)
    assert res.written == [] and res.rejected


def test_one_bad_effect_does_not_block_the_others() -> None:
    s = store()
    res = run(
        mk(1, [eff("context.scale_launch", "nope"), eff("idea.summary", "good")]),
        s,
    )
    assert res.written == ["idea.summary"] and len(res.rejected) == 1


# ------------------------------------------------------------------------------------ add and remove
def test_set_scalar_on_list_slot_becomes_a_list_and_strings_become_named_items() -> None:
    s = store()
    run(mk(1, [eff("audience.segments", "office workers"), eff("scope.features", "Meal plans")]), s)
    seg, feat = s.get("audience.segments"), s.get("scope.features")
    assert seg is not None and seg.value == ["office workers"]
    assert feat is not None and feat.value == [{"name": "Meal plans"}]


def test_add_merges_and_dedupes() -> None:
    s = store()
    run(mk(1, [eff("audience.segments", ["students", "office workers"])]), s)
    res = run(mk(2, [eff("audience.segments", ["Students", "seniors"], "add")]), s)
    rec = s.get("audience.segments")
    assert res.written == ["audience.segments"]
    assert rec is not None and rec.value == ["students", "office workers", "seniors"]
    assert len(rec.history) == 1  # the earlier value is kept


def test_add_onto_an_assumed_default_starts_the_users_own_list() -> None:
    s = store()
    run(
        mk(
            1,
            [eff("audience.segments", ["default one", "default two"])],
            origin="assumption",
            quote="",
        ),
        s,
    )
    run(mk(2, [eff("audience.segments", ["mine"], "add")]), s)
    rec = s.get("audience.segments")
    assert rec is not None and rec.value == ["mine"] and rec.status == "volunteered"


def test_remove_items_and_clear() -> None:
    s = store()
    run(mk(1, [eff("audience.segments", ["a", "b", "c"])]), s)
    res = run(mk(2, [eff("audience.segments", ["B"], "remove")]), s)
    rec = s.get("audience.segments")
    assert res.written == ["audience.segments"]
    assert rec is not None and rec.value == ["a", "c"]
    res = run(mk(3, [{"slot_id": "audience.segments", "value_json": "null", "mode": "remove"}]), s)
    assert res.cleared == ["audience.segments"] and not s.filled("audience.segments")
    again = run(
        mk(4, [{"slot_id": "audience.segments", "value_json": "null", "mode": "remove"}]), s
    )
    assert again.rejected[0].reason == "nothing_to_remove"


def test_remove_on_a_text_slot_needs_the_matching_value() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "tiffin")]), s)
    res = run(mk(2, [eff("idea.summary", "pizza", "remove")]), s)
    assert res.rejected[0].reason == "remove_value_mismatch" and s.filled("idea.summary")
    res = run(mk(3, [eff("idea.summary", "Tiffin", "remove")]), s)
    assert res.cleared == ["idea.summary"]


def test_equal_value_is_unchanged() -> None:
    s = store()
    run(mk(1, [eff("idea.summary", "x")]), s)
    res = run(mk(2, [eff("idea.summary", "x")]), s)
    assert res.unchanged == ["idea.summary"] and res.written == []


# ---------------------------------------------------------------------------------------- reproject
def test_supersede_then_reproject_restores_the_older_active_fact() -> None:
    s = store()
    a = mk(1, [eff("idea.summary", "tiffin service")], quote="tiffin service")
    b = mk(2, [eff("idea.summary", "pizza shop")], quote="pizza shop")
    run(a, s)
    run(b, s)
    rec = s.get("idea.summary")
    assert rec is not None and rec.value == "pizza shop"
    b_old, _ = supersede(b, mk(3, []), now=NOW)
    res = reproject(["idea.summary"], [a, b_old], s, CATALOG, turn_id="evt_2", now=NOW)
    rec = s.get("idea.summary")
    assert rec is not None and rec.value == "tiffin service"
    assert fact_refs(rec) == {"FCT-001"} and rec.tier == "Locked"
    assert res.cleared == ["idea.summary"] and res.written == ["idea.summary"]
    assert any(h.value == "pizza shop" for h in rec.history)  # history kept


def test_reproject_clears_when_no_active_fact_remains() -> None:
    s = store()
    a = mk(1, [eff("idea.summary", "tiffin service")])
    run(a, s)
    gone, _ = supersede(a, mk(2, []), now=NOW)
    res = reproject(["idea.summary"], [gone], s, CATALOG, turn_id="evt_2", now=NOW)
    assert res.cleared == ["idea.summary"] and not s.filled("idea.summary")
    rec = s.get("idea.summary")
    assert rec is not None and rec.tier == "Open" and rec.history  # the old value is in history
    assert all(p.ref.startswith("FCT-") for p in rec.provenance)


def test_reproject_leaves_a_consistent_slot_alone() -> None:
    s = store()
    a = mk(1, [eff("idea.summary", "tiffin")])
    run(a, s)
    before = s.get("idea.summary")
    res = reproject(["idea.summary"], [a], s, CATALOG, turn_id="evt_2", now=NOW)
    assert res.written == [] and res.cleared == [] and s.get("idea.summary") == before


def test_reproject_leaves_a_slot_that_did_not_come_from_facts() -> None:
    from mde_compass.domain.common import Provenance

    s = store()
    s.write(
        "idea.summary",
        "legacy",
        state="confirmed",
        provenance=Provenance(type="user_tap", ref="evt_5"),
    )
    res = reproject(["idea.summary"], [], s, CATALOG, turn_id="evt_2", now=NOW)
    rec = s.get("idea.summary")
    assert rec is not None and rec.value == "legacy" and res.cleared == []


def test_reproject_replays_adds_in_creation_order() -> None:
    s = store()
    f1 = mk(1, [eff("audience.segments", ["a"])])
    f2 = mk(2, [eff("audience.segments", ["b"], "add")])
    f3 = mk(3, [eff("audience.segments", ["c"], "add")])
    for f in (f1, f2, f3):
        run(f, s)
    f2_old, _ = supersede(f2, mk(4, []), now=NOW)
    reproject(["audience.segments"], [f1, f2_old, f3], s, CATALOG, turn_id="evt_9", now=NOW)
    rec = s.get("audience.segments")
    assert rec is not None and rec.value == ["a", "c"]


def test_reproject_unknown_slot_is_reported() -> None:
    res = reproject(["nope"], [], store(), CATALOG, turn_id=None, now=NOW)
    assert res.rejected[0].reason == "unknown_slot"


def test_verified_fact_ids_and_facts_by_slot() -> None:
    a = mk(1, [eff("idea.summary", "x")])
    b = mk(2, [eff("idea.summary", "y")], unverified_quote=True)
    c = mk(3, [eff("audience.segments", ["z"])], origin="assumption", quote="")
    d = mk(4, [eff("idea.summary", "w")], status="superseded")
    assert verified_fact_ids([a, b, c, d]) == {"FCT-001"}
    by_slot = facts_by_slot([a, b, c, d])
    assert [f.id for f in by_slot["idea.summary"]] == ["FCT-001", "FCT-002"]
    assert list(by_slot) == ["idea.summary", "audience.segments"]


# ------------------------------------------------------------------------------------------ property
SLOTS = ("idea.summary", "audience.segments", "scope.features")
LIST_SLOTS = {"audience.segments", "scope.features"}
TOKENS = ("alpha", "beta", "gamma", "delta")


@dataclass(frozen=True)
class Spec:
    origin: str
    unverified_quote: bool
    quote: str
    effects: list[dict[str, object]]


def build(specs: list[Spec]) -> list[Fact]:
    return [
        mk(
            i + 1,
            spec.effects,
            origin=spec.origin,
            unverified_quote=spec.unverified_quote,
            quote=spec.quote,
        )
        for i, spec in enumerate(specs)
    ]


@st.composite
def fact_specs(draw: st.DrawFn) -> list[Spec]:
    n = draw(st.integers(1, 7))
    specs: list[Spec] = []
    for _ in range(n):
        effects = []
        for _ in range(draw(st.integers(1, 2))):
            slot = draw(st.sampled_from(SLOTS))
            mode = draw(
                st.sampled_from(
                    ["set", "add", "remove"] if slot in LIST_SLOTS else ["set", "remove"]
                )
            )
            token = draw(st.sampled_from(TOKENS))
            if mode == "remove":
                value: object = [token] if slot in LIST_SLOTS else None
            else:
                value = [token] if slot in LIST_SLOTS else token
            effects.append(eff(slot, value, mode))
        specs.append(
            Spec(
                origin=draw(st.sampled_from(["stated", "tap", "document", "assumption"])),
                unverified_quote=draw(st.booleans()),
                quote=draw(st.sampled_from(["", "some words"])),
                effects=effects,
            )
        )
    return specs


@settings(max_examples=120, deadline=None)
@given(specs=fact_specs(), data=st.data())
def test_no_locked_slot_rests_on_an_unverified_or_superseded_fact(
    specs: list[Spec], data: st.DataObject
) -> None:
    facts = build(specs)
    s = store()
    for f in facts:
        project(f, s, CATALOG, turn_id="evt_1", now=NOW)
    flips = data.draw(
        st.lists(
            st.sampled_from(["keep", "superseded", "disputed", "not_used"]),
            min_size=len(facts),
            max_size=len(facts),
        )
    )
    changed: list[Fact] = []
    for f, how in zip(facts, flips, strict=True):
        changed.append(
            f
            if how == "keep"
            else f.model_copy(
                update={"status": how, "not_used_reason": "r" if how == "not_used" else ""}
            )
        )
    affected = [e.slot_id for f in facts for e in f.effects]
    reproject(affected, changed, s, CATALOG, turn_id="evt_2", now=NOW)

    verified = verified_fact_ids(changed)
    active = {f.id for f in changed if f.status == "active"}
    for slot_id, rec in s.all().items():
        refs = [p.ref for p in rec.provenance]
        assert all(r.startswith("FCT-") for r in refs), (slot_id, refs)
        if s.filled(slot_id):
            assert set(refs) <= active, (slot_id, refs, active)
        if rec.tier == "Locked":
            evidence = [p.ref for p in rec.provenance if p.is_evidence()]
            assert evidence and set(evidence) <= verified, (slot_id, evidence, verified)


@settings(max_examples=60, deadline=None)
@given(specs=fact_specs())
def test_projection_never_writes_a_non_fact_ref_and_is_replayable(
    specs: list[Spec],
) -> None:
    facts = build(specs)
    s = store()
    for f in facts:
        project(f, s, CATALOG, turn_id="evt_1", now=NOW)
    for rec in s.all().values():
        assert all(p.ref.startswith("FCT-") for p in rec.provenance)
        if rec.tier == "Locked":
            assert any(p.is_evidence() for p in rec.provenance)
    # Reprojecting with every fact still active changes no slot value.
    before = {k: r.value for k, r in s.all().items()}
    reproject(list(before), facts, s, CATALOG, turn_id="evt_3", now=NOW)
    after = {k: r.value for k, r in s.all().items() if s.filled(k)}
    assert after == {k: v for k, v in before.items() if s.filled(k)}
