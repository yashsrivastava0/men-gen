"""The structure of the app (ADR 0017): four optional Brief sections that change no stored hash, are left out of the dump
while empty, and are edited through the ordinary patch path (so undo and history work)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import (
    LIST_SECTIONS,
    OPTIONAL_SECTIONS,
    READ_ONLY_SECTIONS,
    SECTION_KEYS,
    Brief,
)
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.structure import (
    STRUCTURE_SECTIONS,
    EntityRecord,
    FlowRecord,
    FlowStep,
    ModuleRecord,
    ScreenEntityUse,
    ScreenRecord,
    access_allows,
)
from mde_compass.engine.patch import apply_ops, build_change_set
from mde_compass.engine.validate import validate_brief

FIXTURES = Path(__file__).parent / "fixtures"
CAT = load_catalog()
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def structured(brief: Brief) -> Brief:
    """A small, valid structure on top of a Brief: two modules, three screens, a flow and an entity."""
    out = brief.model_copy(deep=True)
    out.ids = IdLedger()
    for prefix, n in (("MOD", 2), ("SCR", 3), ("FLW", 1), ("ENT", 1)):
        for _ in range(n):
            out.ids.allocate(prefix)
    out.modules = [
        ModuleRecord(id="MOD-001", name="Ordering", purpose="Browse and order", entry="SCR-001"),
        ModuleRecord(id="MOD-002", name="Account", purpose="Sign in and profile"),
    ]
    out.screens = [
        ScreenRecord(id="SCR-001", name="Menu", module="MOD-001", pattern="list"),
        ScreenRecord(
            id="SCR-002",
            name="Order details",
            module="MOD-001",
            pattern="detail",
            entities=[ScreenEntityUse(entity="ENT-001", use="view")],
        ),
        ScreenRecord(id="SCR-003", name="Order details", module="MOD-002", pattern="detail"),
    ]
    out.flows = [
        FlowRecord(
            id="FLW-001",
            name="Order a dish",
            steps=[FlowStep(screen="SCR-001", action="pick"), FlowStep(screen="SCR-002")],
        )
    ]
    out.entities = [EntityRecord(id="ENT-001", key="order", name="Order", module="MOD-001")]
    return out


# ------------------------------------------------------------------------------- hash stability (ADR 0017)


@pytest.mark.parametrize(
    "name", ["brief_before_interview_memory.json", "real_brief_delhi.json", "real_brief_pune.json"]
)
def test_a_brief_stored_before_the_structure_existed_keeps_its_hashes(name: str) -> None:
    fixture = load(name)
    brief = Brief.model_validate(fixture["brief"])
    assert brief_hash(brief) == fixture["brief_hash"]
    assert section_hashes(brief) == fixture["section_hashes"]
    # none of the four sections is part of the hashes while it is empty
    assert not set(section_hashes(brief)) & set(STRUCTURE_SECTIONS)


def test_the_approved_baseline_of_a_real_brief_still_matches() -> None:
    fixture = load(
        "real_brief_pune.json"
    )  # a Brief that was approved at Gate 1, with its section hashes
    brief = Brief.model_validate(fixture["brief"])
    assert brief_hash(brief) == fixture["approved_hash"]
    assert section_hashes(brief) == fixture["approved_section_hashes"]


@pytest.mark.parametrize("name", ["real_brief_delhi.json", "real_brief_pune.json"])
def test_a_brief_without_a_structure_is_dumped_without_the_four_keys(name: str) -> None:
    brief = Brief.model_validate(load(name)["brief"])
    dumped = brief.model_dump(mode="json")
    assert not set(STRUCTURE_SECTIONS) & set(
        dumped
    )  # code from before this change can still read what is stored
    assert Brief.model_validate(dumped) == brief


def test_a_structure_joins_the_hashes_and_only_its_own_sections_move() -> None:
    before = Brief.model_validate(load("real_brief_pune.json")["brief"])
    after = structured(before)
    assert brief_hash(after) != brief_hash(before)
    old, new = section_hashes(before), section_hashes(after)
    assert set(new) - set(old) == set(STRUCTURE_SECTIONS)
    assert {k: v for k, v in new.items() if k in old} == old  # every other section hashes as it did


def test_an_edit_inside_one_structure_section_voids_only_that_section() -> None:
    brief = structured(Brief())
    other = brief.model_copy(deep=True)
    other.screens[0] = other.screens[0].model_copy(update={"name": "Dishes"})
    before, after = section_hashes(brief), section_hashes(other)
    assert [k for k in SECTION_KEYS if before.get(k) != after.get(k)] == ["screens"]


def test_the_structure_round_trips_through_the_dump() -> None:
    brief = structured(Brief())
    dumped = brief.model_dump(mode="json")
    assert set(STRUCTURE_SECTIONS) <= set(dumped)
    assert Brief.model_validate(dumped) == brief
    assert brief_hash(Brief.model_validate(dumped)) == brief_hash(brief)


def test_the_four_sections_are_optional_list_sections_that_are_not_read_only() -> None:
    for section in STRUCTURE_SECTIONS:
        assert section in OPTIONAL_SECTIONS and section in LIST_SECTIONS
        assert section not in READ_ONLY_SECTIONS  # undo replays through the patch engine


# ------------------------------------------------------------------------------------------------ records


def test_records_are_strict_and_start_as_proposed() -> None:
    assert ModuleRecord(id="MOD-001", name="x").decision == "proposed"
    with pytest.raises(ValidationError):
        ScreenRecord.model_validate(
            {"id": "SCR-001", "name": "x", "route": "/menu"}
        )  # routes are a later stage
    with pytest.raises(ValidationError):
        EntityRecord.model_validate({"id": "ENT-001", "name": "x", "access": {"ROL-001": "owner"}})
    with pytest.raises(ValidationError):
        ScreenEntityUse.model_validate({"entity": "ENT-001", "use": "delete"})


def test_an_access_level_covers_what_a_screen_does_with_an_entity() -> None:
    assert (
        access_allows("manage", "edit")
        and access_allows("edit", "create")
        and access_allows("view", "view")
    )
    assert not access_allows("view", "edit") and not access_allows("none", "view")
    assert not access_allows("create", "edit")


def test_the_id_ledger_knows_the_four_prefixes_and_still_refuses_others() -> None:
    ledger = IdLedger()
    assert [ledger.allocate(p) for p in ("MOD", "SCR", "FLW", "ENT")] == [
        "MOD-001",
        "SCR-001",
        "FLW-001",
        "ENT-001",
    ]
    assert ledger.allocate("SCR") == "SCR-002"
    with pytest.raises(ValueError):
        ledger.allocate("XYZ")


# -------------------------------------------------------------------------------- validation of the pointers


def test_a_valid_structure_has_no_problems_and_a_dangling_pointer_is_named() -> None:
    brief = structured(Brief())
    assert validate_brief(brief, CAT) == []
    brief.screens[0] = brief.screens[0].model_copy(
        update={"module": "MOD-009", "roles": ["ROL-007"]}
    )
    problems = validate_brief(brief, CAT)
    assert "screens.SCR-001: module 'MOD-009' does not exist" in problems
    assert "screens.SCR-001: role 'ROL-007' does not exist" in problems


def test_an_entity_whose_owner_module_does_not_exist_is_reported() -> None:
    brief = structured(Brief())
    brief.entities[0] = brief.entities[0].model_copy(update={"module": "MOD-042"})
    assert "entities.ENT-001: module 'MOD-042' does not exist" in validate_brief(brief, CAT)


def test_a_locked_structure_record_needs_evidence_like_every_other_record() -> None:
    brief = structured(Brief())
    brief.modules[0] = brief.modules[0].model_copy(update={"tier": "Locked"})
    assert any(
        "modules.MOD-001: tier Locked needs evidence" in p for p in validate_brief(brief, CAT)
    )


def test_an_id_in_the_tombstones_or_ahead_of_the_ledger_is_reported() -> None:
    brief = structured(Brief())
    brief.ids.tombstone("SCR-002")
    brief.ids.counters["FLW"] = 0
    problems = validate_brief(brief, CAT)
    assert any("SCR-002 is in the tombstones" in p for p in problems)
    assert any("FLW-001 is ahead of the id ledger" in p for p in problems)


# -------------------------------------------------------------------- edits go through the ordinary patch path


def apply(brief: Brief, *ops: PatchOp) -> Any:
    return apply_ops(brief, list(ops), CAT, now=NOW, turn_id="evt_1")


def test_a_screen_can_be_renamed_moved_and_undone() -> None:
    brief = structured(Brief())
    res = apply(
        brief,
        PatchOp(op="set", path="screens.SCR-001.name", value="Dishes"),
        PatchOp(op="set", path="screens.SCR-001.module", value="MOD-002"),
    )
    assert res.errors == []
    screen = next(s for s in res.brief.screens if s.id == "SCR-001")
    assert (screen.name, screen.module) == ("Dishes", "MOD-002")
    assert (
        screen.tier == "Locked" and screen.provenance[-1].type == "user_text"
    )  # a typed edit is user evidence
    undone = apply(res.brief, *res.inverse_ops)
    assert undone.errors == []
    assert next(s for s in undone.brief.screens if s.id == "SCR-001").name == "Menu"
    assert next(s for s in undone.brief.screens if s.id == "SCR-001").module == "MOD-001"


def test_a_record_can_be_added_with_an_allocated_id_removed_and_the_removal_undone() -> None:
    brief = structured(Brief())
    added = apply(
        brief,
        PatchOp(op="add", path="modules", value={"name": "Reports", "purpose": "Daily numbers"}),
    )
    assert added.errors == []
    new = next(m for m in added.brief.modules if m.name == "Reports")
    assert new.id == "MOD-003" and added.brief.ids.counters["MOD"] == 3
    removed = apply(added.brief, PatchOp(op="remove", path="modules.MOD-003"))
    assert removed.errors == [] and "MOD-003" in removed.brief.ids.tombstones
    restored = apply(removed.brief, *removed.inverse_ops)
    assert restored.errors == [] and any(m.id == "MOD-003" for m in restored.brief.modules)


def test_the_same_screen_name_in_two_modules_is_fine_but_not_the_same_module_name_twice() -> None:
    brief = structured(Brief())  # "Order details" already exists in two modules
    assert validate_brief(brief, CAT) == []
    ok = apply(
        brief,
        PatchOp(op="add", path="screens", value={"name": "Order details", "module": "MOD-001"}),
    )
    assert ok.errors == []
    dup = apply(brief, PatchOp(op="add", path="modules", value={"name": "ordering"}))
    assert dup.errors and "already exists" in dup.errors[0]


def test_a_patch_that_points_at_nothing_is_refused_not_applied() -> None:
    brief = structured(Brief())
    res = apply(brief, PatchOp(op="set", path="screens.SCR-001.module", value="MOD-099"))
    assert res.errors and "module 'MOD-099' does not exist" in res.errors[0]
    assert res.brief == brief  # all or nothing


def test_a_change_set_over_the_structure_records_the_voided_sections() -> None:
    brief = structured(Brief())
    res = apply(brief, PatchOp(op="set", path="flows.FLW-001.goal", value="Get dinner"))
    cs = build_change_set(
        res,
        id="CHG-001",
        base_version=1,
        source="ui_edit",
        reason="test",
        summary="",
        turn_id="evt_1",
    )
    assert [op.path for op in cs.ops] == ["flows.FLW-001.goal"]
    assert [
        k for k in SECTION_KEYS if section_hashes(brief).get(k) != section_hashes(res.brief).get(k)
    ] == ["flows"]
