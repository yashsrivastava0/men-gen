"""What the person does to the structure (ADR 0017) as patch operations: accept never creates Locked, a typed edit does,
a removal takes what depends on it, every action is ONE change set that undo restores whole."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.common import Provenance
from mde_compass.engine import structure_actions as act
from mde_compass.engine.patch import apply_ops
from mde_compass.engine.structure_testing import sample_app
from mde_compass.engine.validate import validate_brief

CAT = load_catalog()
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
NO_EVIDENCE = act.not_evidence("evt_1")
TYPED = [Provenance(type="user_text", ref="evt_1", quote="my words")]


def apply(brief: Brief, ops: list[PatchOp]):  # type: ignore[no-untyped-def]
    res = apply_ops(brief, ops, CAT, now=NOW, turn_id="evt_1")
    assert res.errors == [], res.errors
    return res


# ----------------------------------------------------------------------------------------------- accept


def test_accepting_a_module_accepts_what_is_in_it_and_nothing_else() -> None:
    brief = sample_app()
    ops = act.accept_ops(brief, "modules", "MOD-003", NO_EVIDENCE)
    res = apply(brief, ops)
    b = res.brief
    assert {m.id for m in b.modules if m.decision == "accepted"} == {"MOD-003"}
    assert {s.id for s in b.screens if s.decision == "accepted"} == {
        "SCR-011",
        "SCR-012",
        "SCR-013",
    }
    assert {e.id for e in b.entities if e.decision == "accepted"} == {
        "ENT-002"
    }  # the one MOD-003 owns
    assert {
        f.id for f in b.flows if f.decision == "accepted"
    } == set()  # FLW-002 starts in another module
    assert all(m.decision == "proposed" for m in b.modules if m.id != "MOD-003")


def test_a_flow_that_stays_inside_the_module_is_accepted_with_it() -> None:
    brief = sample_app()
    ids = act.records_in_module(brief, "MOD-002")
    assert (
        ids["flows"] == ["FLW-001"]
        and "SCR-006" in ids["screens"]
        and ids["entities"] == ["ENT-001", "ENT-003"]
    )


def test_an_accepted_record_is_confirmed_but_never_locked() -> None:
    brief = sample_app()
    res = apply(brief, act.accept_ops(brief, "modules", "MOD-001", NO_EVIDENCE))
    module = next(m for m in res.brief.modules if m.id == "MOD-001")
    assert (module.decision, module.status, module.tier) == ("accepted", "confirmed", "Default")
    assert not any(
        p.is_evidence() for p in module.provenance
    )  # "I looked at it" is not "I said it"


def test_a_typed_edit_is_the_persons_own_words_and_makes_the_record_locked() -> None:
    brief = sample_app()
    res = apply(
        brief, [PatchOp(op="set", path="screens.SCR-001.name", value="Welcome", provenance=TYPED)]
    )
    screen = next(s for s in res.brief.screens if s.id == "SCR-001")
    assert (screen.name, screen.tier, screen.status) == ("Welcome", "Locked", "confirmed")


def test_accepting_what_is_already_accepted_does_nothing_and_accept_all_covers_everything() -> None:
    brief = sample_app()
    first = apply(brief, act.accept_all_ops(brief, NO_EVIDENCE)).brief
    assert all(r.decision == "accepted" for sec in act.SECTION_ROOTS for r in getattr(first, sec))
    assert (
        act.accept_all_ops(first, NO_EVIDENCE) == []
        and act.accept_ops(first, "modules", "MOD-001", NO_EVIDENCE) == []
    )
    assert act.unreviewed_modules(sample_app()) == 3 and act.unreviewed_modules(first) == 0


def test_an_unknown_record_or_section_is_an_action_error() -> None:
    with pytest.raises(act.ActionError):
        act.accept_ops(sample_app(), "modules", "MOD-099", NO_EVIDENCE)
    with pytest.raises(act.ActionError):
        act.accept_ops(sample_app(), "assumptions", "ASM-001", NO_EVIDENCE)


# --------------------------------------------------------------------------------------------------- add


def test_a_new_record_gets_its_id_from_the_ledger_and_its_words_are_evidence() -> None:
    brief = sample_app()
    ops = act.add_ops(
        "screens",
        {
            "name": "Cancel a visit",
            "module": "MOD-002",
            "roles": ["ROL-001"],
            "id": "SCR-900",
            "tier": "Locked",
        },
        TYPED,
    )
    res = apply(brief, ops)
    new = next(s for s in res.brief.screens if s.name == "Cancel a visit")
    assert new.id == "SCR-014" and new.tier == "Locked" and new.provenance[0].quote == "my words"
    assert new.decision == "proposed"  # the person wrote it; they have not yet accepted the module


def test_a_record_without_a_name_cannot_be_added() -> None:
    with pytest.raises(act.ActionError):
        act.add_ops("modules", {"purpose": "x"}, TYPED)
    with pytest.raises(act.ActionError):
        act.add_ops("modules", "just text", TYPED)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ remove


def test_removing_a_module_takes_its_screens_and_the_steps_that_pointed_at_them() -> None:
    brief = sample_app()
    res = apply(brief, act.remove_ops(brief, "modules", "MOD-003", NO_EVIDENCE))
    b = res.brief
    assert "MOD-003" not in {m.id for m in b.modules} and not {
        s for s in b.screens if s.module == "MOD-003"
    }
    assert "FLW-002" not in {
        f.id for f in b.flows
    }  # two of its three steps went: one step is not a journey
    assert "FLW-001" in {f.id for f in b.flows}  # the other journey is untouched
    assert {"MOD-003", "SCR-011", "SCR-012", "SCR-013"} <= set(b.ids.tombstones)
    assert validate_brief(b, CAT) == []  # no pointer is left dangling


def test_a_flow_left_with_one_step_goes_with_the_module_and_the_entity_nobody_uses_goes_too() -> (
    None
):
    brief = sample_app()
    res = apply(brief, act.remove_ops(brief, "modules", "MOD-003", NO_EVIDENCE))
    assert "FLW-002" not in {f.id for f in res.brief.flows}  # one step is not a journey
    assert "ENT-002" not in {
        e.id for e in res.brief.entities
    }  # only the removed module's notes screen used it


def test_an_entity_another_module_still_uses_moves_there_instead_of_going() -> None:
    from mde_compass.domain.structure import ScreenEntityUse

    brief = sample_app()
    i = next(i for i, s in enumerate(brief.screens) if s.id == "SCR-011")
    brief.screens[i] = brief.screens[i].model_copy(
        update={"entities": [ScreenEntityUse(entity="ENT-001", use="view")]}
    )
    res = apply(brief, act.remove_ops(brief, "modules", "MOD-002", NO_EVIDENCE))
    ent = next(
        e for e in res.brief.entities if e.id == "ENT-001"
    )  # the consultation room still shows appointments
    assert ent.module == "MOD-003"
    assert validate_brief(res.brief, CAT) == []


def test_removing_a_screen_fixes_the_module_entry_and_the_flows() -> None:
    brief = sample_app()
    res = apply(
        brief, act.remove_ops(brief, "screens", "SCR-006", NO_EVIDENCE)
    )  # the entry of MOD-002
    b = res.brief
    assert "SCR-006" not in {s.id for s in b.screens}
    assert next(m for m in b.modules if m.id == "MOD-002").entry in {
        s.id for s in b.screens if s.module == "MOD-002"
    }
    assert validate_brief(b, CAT) == []


def test_removing_an_entity_clears_every_pointer_to_it() -> None:
    brief = sample_app()
    res = apply(brief, act.remove_ops(brief, "entities", "ENT-001", NO_EVIDENCE))
    b = res.brief
    assert all(u.entity != "ENT-001" for s in b.screens for u in s.entities)
    assert all(
        f.ref != "ENT-001" for e in b.entities for f in e.fields
    )  # visit_note.appointment pointed at it
    assert validate_brief(b, CAT) == []


def test_the_cascade_is_one_atomic_change_that_undo_restores_exactly() -> None:
    brief = sample_app()
    before = brief.model_dump(mode="json")
    res = apply(brief, act.remove_ops(brief, "modules", "MOD-003", NO_EVIDENCE))
    assert res.brief.model_dump(mode="json") != before
    back = apply(res.brief, res.inverse_ops)
    after = back.brief.model_dump(mode="json")
    for key in ("modules", "screens", "flows", "entities"):
        assert after[key] == before[key], (
            key
        )  # the same records, in the same order, with the same ids


# ----------------------------------------------------------------------------------------------- diff


def test_a_diff_between_two_structures_replays_forward_and_back() -> None:
    before = sample_app()
    after = sample_app()
    after.screens = [
        s.model_copy(update={"name": "Dishes"}) if s.id == "SCR-006" else s
        for s in after.screens
        if s.id != "SCR-013"
    ]
    after.modules.append(
        after.modules[0].model_copy(update={"id": "MOD-004", "name": "Reports", "entry": ""})
    )
    after.ids.counters["MOD"] = 4
    ops, inverse = act.diff_ops(before, after)
    assert {op.op for op in ops} == {"add", "set", "remove"}
    forward = apply(before, ops).brief
    assert [m.id for m in forward.modules] == [m.id for m in after.modules]
    assert next(
        s for s in forward.screens if s.id == "SCR-006"
    ).name == "Dishes" and "SCR-013" not in {s.id for s in forward.screens}
    back = apply(forward, inverse).brief
    assert {s.id: s.name for s in back.screens} == {s.id: s.name for s in before.screens}
    assert [m.id for m in back.modules] == [m.id for m in before.modules]
