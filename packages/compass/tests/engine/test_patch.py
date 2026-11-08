from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hypothesis import event, given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.common import Provenance
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.engine.brief_builder import apply_slots, empty_brief
from mde_compass.engine.patch import apply_ops, build_change_set, invert
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.validate import validate_brief
from mde_core.clock import FakeClock

CAT = load_catalog()
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TAP = Provenance(type="user_tap", ref="evt_1")
PAT = Provenance(type="pattern", ref="families.yaml")


def base_brief() -> Brief:
    s = SlotStore(CAT, clock=FakeClock())
    w: Any = s.write
    w("idea.summary", "an app for a sweets shop", state="confirmed", provenance=TAP)
    w("idea.category", "commerce", state="assumed", provenance=PAT)
    w("context.geo", {"country": "India", "city": "Pune"}, state="volunteered", provenance=TAP)
    w("audience.segments", ["families"], state="volunteered", provenance=TAP)
    w(
        "apps.list",
        [{"name": "Customer app"}, {"name": "Shop app"}],
        state="volunteered",
        provenance=TAP,
    )
    w(
        "roles.list",
        [{"name": "Customer"}, {"name": "Shop owner"}],
        state="volunteered",
        provenance=TAP,
    )
    w(
        "scope.features",
        [
            {
                "name": "Order tracking",
                "status": "in",
                "priority": "must",
                "apps": ["Customer app"],
            },
            {"name": "Group ordering", "status": "in", "priority": "nice"},
            {"name": "Wishlist", "status": "later"},
        ],
        state="volunteered",
        provenance=TAP,
    )
    w(
        "glossary.terms",
        [{"term": "mithai", "synonyms": ["sweets"]}],
        state="volunteered",
        provenance=TAP,
    )
    w("brand.name", "Mithai House", state="volunteered", provenance=TAP)
    return apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)


def run(b: Brief, *ops: PatchOp, turn: str | None = "evt_7", actor: Any = "user_text") -> Any:
    return apply_ops(b, list(ops), CAT, now=NOW, turn_id=turn, actor=actor)


def set_(path: str, value: Any) -> PatchOp:
    return PatchOp(op="set", path=path, value=value)


def test_set_dict_item_writes_user_provenance_and_history() -> None:
    b = base_brief()
    r = run(b, set_("idea.summary", "a sweets shop with delivery"))
    assert r.ok and r.brief.version == b.version + 1
    item = r.brief.idea["summary"]
    assert item.value == "a sweets shop with delivery"
    assert item.status == "confirmed" and item.tier == "Locked" and item.confidence == 1.0
    assert item.provenance[0].type == "user_text" and item.provenance[0].ref == "evt_7"
    assert item.history[-1].value == "an app for a sweets shop"
    assert "was Locked" in item.history[-1].reason  # touching a Locked item is recorded
    assert r.ops[0].old_value == "an app for a sweets shop"
    assert r.voided_sections == ["idea"]
    assert b.idea["summary"].value == "an app for a sweets shop"  # input untouched


def test_set_without_turn_id_is_not_locked() -> None:
    r = run(base_brief(), set_("idea.summary", "x"), turn=None)
    assert r.ok and r.brief.idea["summary"].tier == "Default"


def test_assumed_actor_cannot_overwrite_user_value_but_can_fill_empty() -> None:
    b = base_brief()
    r = run(b, set_("idea.summary", "x"), actor="assumed")
    assert not r.ok and r.brief == b and "user-given" in r.errors[0]
    r2 = run(b, set_("constraints.offline", "offline_basic"), actor="pattern")
    assert (
        r2.ok
        and r2.brief.constraints["offline"].tier == "Default"
        and r2.brief.constraints["offline"].status == "assumed"
    )


def test_record_field_edit_and_whole_record_inverse() -> None:
    b = base_brief()
    fid = b.scope[1].id
    r = run(b, set_(f"scope.{fid}.priority", "must"))
    assert r.ok
    f = r.brief.scope[1]
    assert f.priority == "must" and f.status == "confirmed" and f.tier == "Locked"
    assert f.history[-1].value == "nice" and r.ops[0].old_value == "nice"
    assert r.voided_sections == ["scope"]
    assert brief_hash(apply_ops(r.brief, r.inverse_ops, CAT, now=NOW).brief) == brief_hash(b)


def test_invalid_ops_leave_the_brief_unchanged() -> None:
    b = base_brief()
    fid = b.scope[0].id
    bad = [
        set_("nowhere.x", "y"),
        set_("idea.bogus", "y"),
        set_("scope.FTR-999.priority", "must"),
        set_(f"scope.{fid}.priority", "urgent"),
        set_(f"scope.{fid}.id", "FTR-100"),
        set_(f"scope.{fid}.tier", "Locked"),
        set_("idea.summary", 5),
        set_("idea.summary", ""),
        PatchOp(op="clear", path="idea.category.x"),
        PatchOp(op="remove", path=f"scope.{fid}.priority"),
        PatchOp(op="add", path="scope", value="not a record"),
        set_(f"scope.{fid}.scope_status", "in"),  # fine on its own...
    ]
    for op in bad[:-1]:
        r = run(b, op)
        assert not r.ok and r.brief == b and r.ops == [] and r.inverse_ops == []
    # one bad op among good ones applies nothing
    r = run(b, set_("idea.summary", "new"), bad[1])
    assert not r.ok and r.brief == b


def test_validation_errors_block_the_patch() -> None:
    b = base_brief()
    fid = b.scope[0].id
    r = run(b, set_(f"scope.{fid}.capability_tier", "C"))
    assert not r.ok and r.brief == b and any("tier C" in e for e in r.errors)
    r = run(b, set_(f"scope.{fid}.apps", ["APP-404"]))
    assert not r.ok and any("APP-404" in e for e in r.errors)


def test_clear_dict_item_and_undo_restores_hash() -> None:
    b = base_brief()
    r = run(b, PatchOp(op="clear", path="experience.brand.name"))
    assert r.ok and "brand.name" not in r.brief.experience and r.ops[0].old_value == "Mithai House"
    back = apply_ops(r.brief, r.inverse_ops, CAT, now=NOW)
    assert back.ok and brief_hash(back.brief) == brief_hash(b)
    assert back.brief.experience["brand.name"] == b.experience["brand.name"]


def test_remove_tombstones_and_undo_restores_same_id() -> None:
    b = base_brief()
    fid = b.scope[1].id
    r = run(b, PatchOp(op="remove", path=f"scope.{fid}"))
    assert r.ok and [f.id for f in r.brief.scope] == [b.scope[0].id, b.scope[2].id]
    assert fid in r.brief.ids.tombstones
    # a fresh add never takes the tombstoned id
    added = run(r.brief, PatchOp(op="add", path="scope", value={"name": "Reviews"}))
    assert added.ok and added.brief.scope[-1].id not in (fid, b.scope[0].id, b.scope[2].id)
    # undo restores the SAME id (and position) and removes the tombstone
    back = apply_ops(r.brief, r.inverse_ops, CAT, now=NOW)
    assert back.ok and fid not in back.brief.ids.tombstones
    assert [f.id for f in back.brief.scope] == [f.id for f in b.scope]
    assert brief_hash(back.brief) == brief_hash(b)
    err = run(r.brief, set_(f"scope.{fid}.priority", "must")).errors[
        0
    ]  # removed ids cannot be edited
    assert "removed" in err


def test_add_allocates_id_and_inverse_is_remove() -> None:
    b = base_brief()
    r = run(b, PatchOp(op="add", path="scope", value={"name": "Reviews", "intent": "rate sweets"}))
    assert r.ok
    new = r.brief.scope[-1]
    assert new.id == "FTR-004" and new.status == "confirmed" and new.provenance[0].ref == "evt_7"
    assert r.ops[0].path == "scope.FTR-004" and r.inverse_ops[0].op == "remove"
    undone = apply_ops(r.brief, r.inverse_ops, CAT, now=NOW)
    assert undone.ok and brief_hash(undone.brief) == brief_hash(b)
    assert "FTR-004" in undone.brief.ids.tombstones
    dup = run(b, PatchOp(op="add", path="scope", value={"name": "order TRACKING"}))
    assert not dup.ok


def test_add_with_explicit_unknown_id_and_live_id_rejected() -> None:
    b = base_brief()
    live = run(b, PatchOp(op="add", path="scope", value={"id": b.scope[0].id, "name": "Other"}))
    assert not live.ok
    bad = run(b, PatchOp(op="add", path="scope", value={"id": "APP-009", "name": "Other"}))
    assert not bad.ok


def test_glossary_addressed_by_name() -> None:
    b = base_brief()
    r = run(b, set_("glossary.mithai.synonyms", ["sweets", "desserts"]))
    assert r.ok and r.brief.glossary[0].synonyms == ["sweets", "desserts"]
    back = apply_ops(r.brief, r.inverse_ops, CAT, now=NOW)
    assert brief_hash(back.brief) == brief_hash(b)
    r2 = run(b, PatchOp(op="remove", path="glossary.mithai"))
    assert r2.ok and r2.brief.glossary == []
    assert brief_hash(apply_ops(r2.brief, r2.inverse_ops, CAT, now=NOW).brief) == brief_hash(b)


def test_clear_record_field_resets_to_default() -> None:
    b = base_brief()
    fid = b.scope[0].id
    r = run(b, PatchOp(op="clear", path=f"scope.{fid}.apps"))
    assert r.ok and r.brief.scope[0].apps == []
    assert brief_hash(apply_ops(r.brief, r.inverse_ops, CAT, now=NOW).brief) == brief_hash(b)
    assert not run(
        b, PatchOp(op="clear", path=f"scope.{fid}.name")
    ).ok  # required field, no default


def test_voided_sections_only_touched_ones() -> None:
    b = base_brief()
    r = run(b, set_("idea.summary", "x"), set_(f"scope.{b.scope[0].id}.intent", "see the order"))
    assert set(r.voided_sections) == {"idea", "scope"}
    after = section_hashes(r.brief)
    before = section_hashes(b)
    assert all(after[k] == before[k] for k in after if k not in {"idea", "scope"})


def test_change_set_and_invert() -> None:
    b = base_brief()
    r = run(
        b, set_("idea.summary", "x"), set_("context.geo", {"country": "India", "city": "Mumbai"})
    )
    cs = build_change_set(
        r,
        id="CHG-001",
        base_version=b.version,
        source="ui_edit",
        reason="r",
        summary="s",
        turn_id="evt_7",
    )
    assert (
        cs.new_version == b.version + 1
        and cs.ops == r.ops
        and cs.voided_sections == r.voided_sections
    )
    inv = invert(cs)
    undone = apply_ops(r.brief, inv, CAT, now=NOW)
    assert undone.ok and brief_hash(undone.brief) == brief_hash(b)
    cs2 = build_change_set(
        undone,
        id="CHG-002",
        base_version=r.brief.version,
        source="undo",
        reason="",
        summary="",
        turn_id=None,
        undoes="CHG-001",
    )
    assert cs2.undoes == "CHG-001" and cs2.new_version == r.brief.version + 1
    assert validate_brief(undone.brief, CAT) == []


# ----------------------------------------------------------------------------------- properties
FIELD_VALUES: dict[str, list[Any]] = {
    "priority": ["must", "nice", "later"],
    "scope_status": ["in", "later", "out", "unsupported"],
    "intent": ["see the order", "x", "one two three"],
}


@st.composite
def good_ops(draw: st.DrawFn, b: Brief) -> list[PatchOp]:
    ops: list[PatchOp] = []
    live = [f.id for f in b.scope]
    for _ in range(draw(st.integers(1, 4))):
        kind = draw(
            st.sampled_from(["field", "dict", "clear", "remove", "add", "gloss", "cleardict"])
        )
        if kind == "field" and live:
            fid = draw(st.sampled_from(live))
            name = draw(st.sampled_from(sorted(FIELD_VALUES)))
            ops.append(set_(f"scope.{fid}.{name}", draw(st.sampled_from(FIELD_VALUES[name]))))
        elif kind == "dict":
            ops.append(set_("idea.summary", draw(st.text(min_size=1, max_size=8))))
        elif kind == "clear" and live:
            ops.append(PatchOp(op="clear", path=f"scope.{draw(st.sampled_from(live))}.intent"))
        elif kind == "remove" and live:
            fid = draw(st.sampled_from(live))
            live = [x for x in live if x != fid]
            ops.append(PatchOp(op="remove", path=f"scope.{fid}"))
        elif kind == "add":
            ops.append(
                PatchOp(
                    op="add",
                    path="scope",
                    value={"name": draw(st.text("abcdefgh", min_size=1, max_size=3))},
                )
            )
        elif kind == "gloss":
            ops.append(
                set_(
                    "glossary.mithai.synonyms",
                    draw(st.lists(st.text("xyz", min_size=1, max_size=3), max_size=3)),
                )
            )
        elif kind == "cleardict":
            ops.append(PatchOp(op="clear", path="experience.brand.name"))
    return ops


@settings(max_examples=120, deadline=None)
@given(st.data())
def test_property_apply_then_inverse_restores_hash_and_ids_never_reused(
    data: st.DataObject,
) -> None:
    b = base_brief()
    ops = data.draw(good_ops(b))
    r = run(b, *ops)
    event("ok" if r.ok else "rejected")
    if not r.ok:
        assert r.brief == b  # invalid combinations (duplicates, double clear...) change nothing
        return
    assert validate_brief(r.brief, CAT) == []
    # ids: never reused, tombstones never live
    live = {f.id for f in r.brief.scope}
    assert not live & set(r.brief.ids.tombstones)
    for rec in r.brief.scope:
        assert int(rec.id.split("-")[1]) <= r.brief.ids.counters["FTR"]
    back = apply_ops(r.brief, r.inverse_ops, CAT, now=NOW)
    assert back.ok, back.errors
    assert brief_hash(back.brief) == brief_hash(b)
    assert section_hashes(back.brief) == section_hashes(b)
    # tombstones only grow; every originally live id is live again after undo
    assert {f.id for f in b.scope} == {
        f.id for f in back.brief.scope if f.id in {x.id for x in b.scope}
    }
    assert set(r.brief.ids.tombstones) >= set(b.ids.tombstones)


@settings(max_examples=60, deadline=None)
@given(st.data(), st.integers(0, 3))
def test_property_one_invalid_op_changes_nothing(data: st.DataObject, pos: int) -> None:
    b = base_brief()
    ops = data.draw(good_ops(b))
    ops.insert(min(pos, len(ops)), set_("scope.FTR-999.priority", "must"))
    r = run(b, *ops)
    assert not r.ok and r.brief == b and r.ops == []
