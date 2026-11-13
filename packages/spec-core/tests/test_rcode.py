from mde_spec_core.callkit.ladder import FixKind
from mde_spec_core.graph import Entity, Prov, StateSpec
from mde_spec_core.ids import IdRegistry
from mde_spec_core.rcode import (
    apply_synonyms,
    downgrade_unevidenced_locked,
    drop_unknown_optional,
    instantiate_acceptance,
    instantiate_behaviors,
    instantiate_states,
    mint_missing_ids,
    run_rcode,
    sort_bytes,
)
from mde_spec_core.synth import make_graph


def test_fix1_sort_bytes_normalises_and_logs():
    files = {"a.md": "x  \r\ny\r\n\r\n", "b.md": "ok\n"}
    log = sort_bytes(files)
    assert files["a.md"] == "x\ny\n" and len(log) == 1 and log.changes[0].kind == FixKind.SORT_BYTES


def test_fix2_mints_a_missing_id_but_never_fixes_a_duplicate():
    reg = IdRegistry()
    reg.carry("SCR-001")
    items = [{"id": ""}, {"id": "SCR-001"}]
    log = mint_missing_ids(items, "SCR", reg)
    assert items[0]["id"] == "SCR-S001" and items[1]["id"] == "SCR-001" and len(log) == 1


def test_fix3_missing_state_acceptance_and_behaviour_come_from_the_library():
    g = make_graph("small")
    s = g.screens["SCR-003"]
    s.states = [x for x in s.states if x.kind != "offline"]
    s.acceptance = s.acceptance[:1]
    for b in list(g.behaviors):
        del g.behaviors[b]
    log = instantiate_states(g)
    log2 = instantiate_acceptance(g)
    log3 = instantiate_behaviors(g, set(g.meta.facts))
    assert any(st.kind == "offline" and st.origin == "library" for st in s.states)
    assert len(s.acceptance) >= 3 and len(log) >= 1 and len(log2) >= 1 and len(log3) >= 1
    assert all(
        b.provenance[0].kind == "library" and b.tier == "default" for b in g.behaviors.values()
    )


def test_fix4_only_exact_synonyms_are_mapped():
    g = make_graph("tiny")
    s = g.screens["SCR-002"]
    s.screen_type = "list_view"  # an exact synonym in the table
    s.actions[0].intent = "totally_unknown_intent"  # no synonym: left for the R-ask
    log = apply_synonyms(g)
    assert s.screen_type == "list" and s.actions[0].intent == "totally_unknown_intent"
    assert any(c.kind == FixKind.EXACT_SYNONYM for c in log.changes)


def test_fix5_drops_unknown_optional_fields_and_logs():
    raw = {"id": "ENT-001", "name": "x", "colour_of_header": "blue"}
    log = drop_unknown_optional(raw, Entity, "ENT-001")
    assert "colour_of_header" not in raw and len(log) == 1


def test_fix6_locked_without_evidence_becomes_default():
    g = make_graph("tiny")
    s = g.screens["SCR-002"]
    s.tier = "locked"
    s.provenance = [Prov(kind="research", ref="RI-1")]
    log = downgrade_unevidenced_locked(g)
    assert s.tier == "default" and len(log) == 1
    e = g.entities["ENT-001"]
    e.tier = "locked"
    e.provenance = [Prov(kind="user", ref="FTR-001")]
    assert len(downgrade_unevidenced_locked(g)) == 0


def test_run_rcode_is_idempotent():
    g = make_graph("small")
    g.screens["SCR-003"].states = [StateSpec(kind="loading", message="x")]
    first = run_rcode(g, set(g.meta.facts))
    second = run_rcode(g, set(g.meta.facts))
    assert len(first) > 0 and len(second) == 0
