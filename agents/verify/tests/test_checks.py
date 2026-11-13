"""The pure-code half: validator findings as Defects, the structure critic's code half on planted defects."""

from __future__ import annotations

import pytest

from mde_spec_core.check import spec_check
from mde_spec_core.planted import STRUCTURAL, Plant
from mde_spec_core.verify_port import LockedFact
from mde_verify_agent import checks

# The planted structural kinds that are ABOUT STRUCTURE (what exists and how it connects): the code half of the
# structure critic must catch every one of them on the graph alone.
STRUCTURE_KINDS = {
    "orphan_screen": "V-04",
    "entity_without_screen": "V-29",
    "action_to_missing_screen": "V-07",
    "role_gating_missing": "V-08",
    "phase1_depends_on_phase2": "V-28",
    "later_feature_has_screen_node": "V-19",
}


def test_the_structure_kinds_are_in_the_planted_catalogue() -> None:
    by_id = {d.id: d.expected for d in STRUCTURAL}
    assert {k: by_id[k] for k in STRUCTURE_KINDS} == STRUCTURE_KINDS


@pytest.mark.parametrize(("kind", "code"), sorted(STRUCTURE_KINDS.items()))
def test_structure_code_half_catches_the_structural_plant(kind: str, code: str, big_graph) -> None:
    defect = next(d for d in STRUCTURAL if d.id == kind)
    g = big_graph.model_copy(deep=True)
    defect.apply(Plant(g))
    found = checks.structure_code_defects(g)
    assert code in {d.code for d in found}, (
        f"{kind}: expected {code}, got {sorted({d.code for d in found})}"
    )
    for d in found:
        assert d.cls in "ABC" and d.change and d.reason and d.node


def test_no_false_positives_on_the_unmodified_graph(big_graph) -> None:
    assert checks.structure_code_defects(big_graph) == []


def test_code_findings_convert_a_check_report_with_repair_classes(big_graph, big_pack) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    report = spec_check(g)
    out = checks.code_findings(g, report)
    v07 = [d for d in out if d.code == "V-07"]
    assert v07 and v07[0].node == "SCR-002" and v07[0].cls in "ABC" and v07[0].severity == "blocker"
    assert all(d.severity != "info" for d in out)
    # without a report the same suite runs on the graph through make_context
    assert any(d.code == "V-07" for d in checks.code_findings(g))
    # conversions are deterministic and sorted blockers first
    assert [d.id for d in out] == [d.id for d in checks.code_findings(g, report)]
    sev = [d.severity for d in out]
    assert sev == sorted(sev, key=["blocker", "warning", "info"].index)


def test_locked_facts_dropped_downgraded_and_changed(big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    rule = next(iter(g.rules.values()), None)
    facts = [
        LockedFact(id="F1", node="RUL-777", text="gone"),
        LockedFact(id="F2", node="SCR-001", text="not locked"),
    ]
    got = {d.id: d for d in checks.locked_fact_defects(g, facts)}
    assert got["S-LOCKED:F1:dropped"].cls == "B"
    assert got["S-LOCKED:F2:tier"].cls == "C"  # never repaired into a Locked fact
    if rule is not None:
        rule.tier = "locked"
        rule.value = "5"
        changed = checks.locked_fact_defects(
            g, [LockedFact(id="F3", node=rule.id, field="value", value="4")]
        )
        assert [(d.cls, d.change) for d in changed] == [("A", "set value back to '4'")]


def test_role_entity_mismatch_only_on_an_explicit_denial(big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    sid = next(
        s
        for s, sc in sorted(g.screens.items())
        if sc.role_ids and checks.bound_fields(sc).keys() - {"<ops>"}
    )
    scr = g.screens[sid]
    eid = sorted(checks.bound_fields(scr).keys() - {"<ops>"})[0]
    ent = g.entities[eid]
    rid = scr.role_ids[0]
    ent.access = [r for r in ent.access if r.role_id != rid]
    assert not [
        d for d in checks.role_entity_defects(g) if d.node == sid and eid in d.id
    ]  # absent: not flagged
    from mde_spec_core.graph import AccessRow

    ent.access.append(AccessRow(role_id=rid, actions=["none"]))
    hit = [d for d in checks.role_entity_defects(g) if d.node == sid and eid in d.id]
    assert hit and hit[0].cls == "B" and hit[0].severity == "blocker"


def test_confirm_rank_defects_are_class_a_resorts() -> None:
    confirm = [
        {"rank": 1, "category": "money", "id": "R1", "text": "fee"},
        {"rank": 2, "category": "other", "id": "R2", "text": "hours"},
        {"rank": 3, "category": "identity", "id": "R3", "text": "kyc"},
    ]
    out = checks.confirm_rank_defects(confirm)
    assert [(d.node, d.cls) for d in out] == [("R3", "A")]
    assert checks.confirm_rank_defects(confirm[:2]) == []


def test_canary_rate_helper() -> None:
    from mde_spec_core.verify_port import CanaryResult

    rs = [CanaryResult(task_id="T1", blocking_questions=["q"]), CanaryResult(task_id="T2")]
    assert checks.canary_rate(rs, 10, 0.3) == (0.1, True)
    assert checks.canary_rate(rs, 2, 0.3)[1] is False
