"""derive/seed.py: the code-only baseline graph. Honest fields, trust grades, release-only screens, valid references."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.derive.pipeline import derive_graph
from mde_spec_agent.derive.seed import research_adapter
from mde_spec_core import vocab
from mde_spec_core.ids import parse_id
from mde_spec_core.validators.base import blockers, run_all
from mde_spec_core.validators.context import make_context

from .conftest import tiny_input


def run_validators(d, research=None):
    ctx = make_context(
        d.graph, research=research_adapter(research), degraded_locales=d.seeded.degraded_locales
    )
    return run_all(ctx)


def test_graph_basics_meta_facts_profile_and_ids(export) -> None:
    d = derive_graph(export("standard"))
    g = d.graph
    assert g.meta.profile == "standard" and g.meta.facts == sorted(d.facts)
    assert (
        g.meta.source_language == "en"
        and g.meta.languages[0] == "en"
        and set(g.meta.languages) == {"mr", "hi", "en"}
    )
    assert d.seeded.degraded_locales == {"mr", "hi"}  # the baseline writes the source language only
    for kind in (
        "features",
        "roles",
        "apps",
        "modules",
        "entities",
        "operations",
        "rules",
        "integrations",
        "screens",
        "stories",
        "personas",
        "flows",
    ):
        for nid in getattr(g, kind):
            assert parse_id(nid) is not None, nid
    # Compass ids are carried; ids code made live in the Spec namespace
    assert "SCR-014" in g.screens and "ENT-006" in g.entities and "FTR-003" in g.features
    assert all(oid.startswith("OP-S") for oid in g.operations)
    assert all(not r.startswith("RUL-0") for r in g.rules)


def test_trust_grades_follow_doc_15_3_4(export) -> None:
    for key in ("quick", "standard", "thorough"):
        g = derive_graph(export(key)).graph
        for f in g.features.values():
            if f.tier == "locked":  # Locked only with a verified quote
                assert f.user_evidenced and any(
                    p.kind == "user" and p.quote_id for p in f.provenance
                ), f.id
        for n in g.all_nodes():
            if n.tier == "locked":
                assert {p.kind for p in n.provenance} & {"user", "compass", "policy"}, n.id
                assert not n.assumed
        assert all(
            s.tier == "default" for s in g.screens.values()
        )  # structure is a proposal: Default
        assert all(r.assumed for r in g.personas.values())


def test_a_later_feature_has_an_outline_and_no_screen_nodes(export) -> None:
    for key in ("standard", "thorough"):
        d = derive_graph(export(key))
        g = d.graph
        later = [f for f in g.features.values() if f.status == "later"]
        assert later
        for f in later:
            assert f.later_reason in ("user", "capacity", "first_release") and f.outline
        in_ids = {f.id for f in g.features.values() if f.status == "in"}
        for s in g.screens.values():
            assert s.status == "in"
            cited = {p.ref for p in s.provenance if p.ref.startswith("FTR-")}
            assert cited <= in_ids, (s.id, cited - in_ids)
    std = derive_graph(export("standard")).graph.features["FTR-008"]
    assert (
        (std.status, std.later_reason) == ("later", "user")
        and "intent: " in std.outline
        and "ENT-008" in std.outline
    )


def test_release_only_screens_and_access_conflicts_are_repaired(export) -> None:
    d = derive_graph(export("standard"))
    assert set(d.graph.screens) == set(d.plan.release_screens)
    # the three access conflicts Compass reports are repaired and recorded for G1.5
    repairs = " ".join(d.seeded.access_repairs)
    assert "SCR-002" in repairs and "SCR-005" in repairs
    roles_of = {s.id: set(s.role_ids) for s in d.graph.screens.values()}
    assert "ROL-001" not in roles_of["SCR-002"]  # the buyer may only `none` the business account


def test_cards_are_bound_to_real_fields_and_use_the_closed_vocabularies(export) -> None:
    g = derive_graph(export("thorough")).graph
    shapes, intents = set(vocab.shapes()), set(vocab.intents())
    for s in g.screens.values():
        assert s.regions and s.states and s.acceptance
        for r in s.regions:
            assert r.shape in shapes
            for b in r.bind:
                if b.startswith("OP-"):
                    assert b in g.operations
                else:
                    eid, fkey = b.split(".", 1)
                    assert any(f.key == fkey for f in g.entities[eid].fields), (s.id, b)
        for a in s.actions:
            assert a.intent in intents and len(a.effect) <= 3
        assert len({r.key for r in s.regions}) == len(s.regions)


def test_actions_are_intents_with_typed_effects_and_roles_may_call_them(export) -> None:
    g = derive_graph(export("standard")).graph
    pay = next(a for a in g.screens["SCR-014"].actions if a.key == "pay")
    assert pay.intent == "pay" and "idempotent" in pay.flags
    assert [st.verb for st in pay.effect] == ["call", "external_flow", "go"]
    op = g.operations[pay.effect[0].target]
    assert op.idempotent and set(op.roles) & set(g.screens["SCR-014"].role_ids)


def test_one_form_with_a_mode_has_a_create_and_an_edit_action(export) -> None:
    g = derive_graph(export("standard")).graph
    form = g.screens["SCR-008"]
    keys = {a.key: a for a in form.actions}
    assert {"save", "save_changes"} <= set(keys)
    assert g.operations[keys["save"].effect[0].target].method == "POST"
    assert g.operations[keys["save_changes"].effect[0].target].method == "PATCH"
    assert sum(1 for a in form.actions if a.importance == "primary") == 1


def test_operations_come_from_entities_and_the_access_matrix(export) -> None:
    g = derive_graph(export("standard")).graph
    ops = {o.name: o for o in g.operations.values() if o.entity == "ENT-006"}
    assert {"list_order", "get_order", "create_order"} <= set(ops)
    assert (
        "update_order" not in ops and "delete_order" not in ops
    )  # Compass gives nobody edit or manage on an order
    assert all(o.errors for o in g.operations.values())
    assert ops["create_order"].method == "POST" and ops["create_order"].idempotent
    assert ops["list_order"].paged


def test_entities_have_typed_fields_lifecycles_and_access(export) -> None:
    g = derive_graph(export("standard")).graph
    order = g.entities["ENT-006"]
    assert order.fields[0].key == "id" and any(f.type == "money" for f in order.fields)
    st = next(f for f in order.fields if f.key == "status")
    assert (
        st.enum_values
        == order.lifecycle.states
        == ["placed", "payment_pending", "paid", "cancelled"]
    )
    ends = {t.to_state for t in order.lifecycle.transitions}
    assert (
        "paid" in ends and "cancelled" in ends and all(t.roles for t in order.lifecycle.transitions)
    )
    assert order.access and {a.role_id for a in order.access} <= set(g.roles)


def test_provider_side_flow_records_are_instantiated_as_assumed(export) -> None:
    for key in ("quick", "standard"):
        g = derive_graph(export(key)).graph
        pay = next(i for i in g.integrations.values() if i.need_key == "payments")
        assert pay.provider_side_flow and pay.interruption and pay.pending_timeout and pay.assumed
        cats = {b.category for b in g.behaviors.values()}
        assert {"interrupted_external_flow", "pending_timeouts"} <= cats
        assert any(
            r.assumed and r.confirm and "pending result" in r.subject for r in g.rules.values()
        )


def test_derived_payment_integration_when_compass_lists_none(export) -> None:
    g = derive_graph(
        export("standard")
    ).graph  # no integration in the Standard export; FTR-003 says "Buyers pay online"
    pay = next(i for i in g.integrations.values() if i.need_key == "payments")
    assert any(p.kind == "user" and p.ref == "FTR-003" for p in pay.provenance)


def test_stories_cite_features_and_assumed_ones_are_never_must(export) -> None:
    g = derive_graph(export("thorough")).graph
    in_feats = {f.id for f in g.features.values() if f.status == "in"}
    cited = {fid for s in g.stories.values() for fid in s.feature_ids}
    assert cited <= in_feats
    for s in g.stories.values():
        assert not (s.assumed and s.voice == "must")
    assert all(r.id for r in g.personas.values()) and len(g.personas) == len(g.roles)


def test_rules_carry_user_numbers_as_locked_with_a_quote(export) -> None:
    g = derive_graph(export("standard")).graph
    commission = next(r for r in g.rules.values() if "commission" in r.statement)
    assert commission.value == "4" and commission.unit == "%" and commission.tier == "locked"
    assert any(p.kind == "user" and p.ref.startswith("FCT-") for p in commission.provenance)


def test_navigation_is_reachable_and_assumed_links_are_recorded(export) -> None:
    for key in ("quick", "standard", "thorough"):
        d = derive_graph(export(key))
        assert not [f for f in run_validators(d) if f.code == "V-04" and f.severity == "blocker"], (
            key
        )
        for rid, r in d.graph.roles.items():
            assert r.home_screen in d.graph.screens, rid
    assert isinstance(d.seeded.nav_assumed, list)


def test_copy_keys_are_namespaced_and_source_copy_is_complete(export) -> None:
    g = derive_graph(export("quick")).graph
    src = g.locale_copy[g.meta.source_language]
    for s in g.screens.values():
        for k in s.copy_keys:
            assert k.startswith(s.id.lower().replace("-", "_") + ".") or k.startswith("lib.")
            assert k in src


def test_the_baseline_has_no_validator_blockers_that_code_could_have_fixed(export) -> None:
    allowed = {
        "standard": {"V-19", "V-40"}
    }  # one Compass screen that serves no feature (see the PS-2 report)
    for key in ("quick", "standard", "thorough"):
        d = derive_graph(export(key))
        codes = {f.code for f in blockers(run_validators(d))}
        assert codes <= allowed.get(key, set()), (key, codes)


def test_derivation_is_deterministic(export) -> None:
    a, b = derive_graph(export("thorough")), derive_graph(export("thorough"))
    assert a.graph.canonical_text() == b.graph.canonical_text()
    assert a.graph.content_hash() == b.graph.content_hash()


def test_tiny_input_end_to_end() -> None:
    d = derive_graph(tiny_input())
    g = d.graph
    assert Counter(s.screen_type for s in g.screens.values())["form"] >= 1
    assert set(g.roles) == {"ROL-001", "ROL-002"}
    assert not blockers(run_validators(d))
