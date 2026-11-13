"""`plan` then `product` through the real Orchestrator on the three real exports (quick yoga, standard produce, thorough
freight), mock mode: valid patches, gate 1 clean, coverage, provider-side flows, no form words, deterministic bytes."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.derive.seed import research_adapter
from mde_spec_core import libraries
from mde_spec_core.validators.base import blockers, run_all
from mde_spec_core.validators.context import make_context

from .conftest import PROFILE, baseline

JOIN = {
    "V-01", "V-02", "V-03", "V-05", "V-06", "V-08", "V-09", "V-10", "V-11", "V-17", "V-19", "V-28",
    "V-29", "V-30", "V-31", "V-33", "V-35", "V-36", "V-40", "V-44", "V-45",
}  # fmt: skip


def test_the_two_steps_finish_and_the_graph_passes_gate_1_and_the_join_checks(env, key):
    ctx = env.ctx(key)
    out = env.run(ctx)
    assert out.status == "awaiting_g2", out
    g = ctx.graph
    assert g.meta.profile == PROFILE[key]
    gate1 = run_all(make_context(g.model_copy(deep=True)), gate=1)
    assert [f.code for f in blockers(gate1)] == []
    join = run_all(make_context(g.model_copy(deep=True)), gate=3, only=JOIN)
    assert [
        (f.code, f.where) for f in blockers(join)
    ] == []  # V-08 V-09 V-10 V-11 V-19 V-28 V-29 V-35 V-40 and friends
    assert not [
        f for f in join if f.code == "V-40" and f.severity == "warning"
    ]  # acceptance everywhere
    assert ctx.flags == []  # nothing degraded in mock mode
    assert not any(n.degraded for n in g.all_nodes())


def test_every_in_feature_has_a_story_and_every_story_has_acceptance(env, key):
    ctx = env.ctx(key)
    env.run(ctx)
    g = ctx.graph
    with_story = {f for st in g.stories.values() for f in st.feature_ids}
    in_features = {f.id for f in g.features.values() if f.status == "in"}
    assert in_features <= with_story
    for st in g.stories.values():
        assert st.acceptance, st.id
        assert not (st.assumed and st.voice == "must"), st.id
        assert st.policy_id or any(
            g.features[f].status == "in" for f in st.feature_ids if f in g.features
        )
    cited = {s for st in g.stories.values() for s in st.screen_ids}
    assert {s.id for s in g.screens.values() if not s.system} <= cited


def test_every_provider_side_integration_has_interruption_and_pending_timeout(env, key):
    ctx = env.ctx(key)
    env.run(ctx)
    provider_side = [i for i in ctx.graph.integrations.values() if i.provider_side_flow]
    assert provider_side  # all three exports take payments
    for i in provider_side:
        assert i.interruption.strip() and i.pending_timeout.strip() and i.flow.strip(), i.id
        assert i.events, i.id
        assert i.mock_behaviour.strip()


def test_custom_operations_are_typed_gated_and_use_the_closed_error_codes(env, key):
    ctx = env.ctx(key)
    env.run(ctx)
    g = ctx.graph
    custom = [o for o in g.operations.values() if o.kind == "custom"]
    assert custom
    from mde_spec_agent.lanes.product.kit import vocab

    for o in custom:
        assert o.roles and set(o.roles) <= set(g.roles), o.id
        assert o.entity in g.entities and o.errors and o.path.startswith("/")
        assert {e.code for e in o.errors} <= set(vocab.error_codes())
        assert (
            o.tier != "locked" and o.assumed
        )  # a model never writes Locked; this one rests on a lifecycle, assumed
        assert {"id", "status"} <= {f.key for f in o.response}
    paths = Counter((o.method, o.path) for o in g.operations.values())
    assert max(paths.values()) == 1


def test_no_layout_or_widget_word_in_anything_the_lane_wrote(env, key):
    base = baseline(key).graph
    ctx = env.ctx(key)
    env.run(ctx)
    g = ctx.graph

    def words(text: str) -> set[str]:
        return {f.word.lower() for f in libraries.form_word_findings(text)}

    # the user's own words may carry one (a feature called "Pay by card"); the lane adds none of its own
    own: set[str] = set()
    for st in base.stories.values():
        own |= words(f"{st.want} {st.so_that}")
    for f in base.flows.values():
        for s in f.stages:
            own |= words(f"{s.goal} {s.pain}")
        for u in f.unhappy_paths:
            own |= words(u)

    def texts():
        for st in g.stories.values():
            yield st.id, f"{st.want} {st.so_that}"
            for a in st.acceptance:
                yield st.id, f"{a.given} {a.when} {a.then}"
        for o in g.operations.values():
            if o.kind == "custom":
                yield o.id, o.name
        for f in g.flows.values():
            for s in f.stages:
                yield f.id, f"{s.goal} {s.pain}"
            for u in f.unhappy_paths:
                yield f.id, u
        for p in g.personas.values():
            yield p.id, " ".join([p.goal, p.context, p.success, *p.pain_points])
        for i in g.integrations.values():
            yield i.id, f"{i.flow} {i.interruption} {i.pending_timeout} {i.mock_behaviour}"

    added = [(where, w) for where, text in texts() for w in words(text) if w not in own]
    assert added == []


def test_the_product_run_is_deterministic(env, key):
    a, b = env.ctx(key), env.ctx(key)
    env.run(a)
    env.run(b)
    assert a.graph.canonical_text() == b.graph.canonical_text()


def test_compact_apps_get_the_fused_call_and_the_others_one_call_per_part(env, key):
    ctx = env.ctx(key)
    env.run(ctx)
    gw = env.gateway(ctx)
    if key == "quick":
        assert ctx.plan["fused"] is True
        assert gw.count("spec.plan_fused") == 1
        assert gw.count("spec.plan") == gw.count("spec.prod_module") == 0
    else:
        assert ctx.plan["fused"] is False
        assert (
            gw.count("spec.plan") == 1
            and gw.count("spec.prod_module") >= len(ctx.graph.modules) - 1
        )
        assert gw.count("spec.prod_journeys") >= 1 and gw.count("spec.prod_integ") >= 1


def test_the_plan_step_leaves_what_g15_needs_in_ctx_plan(env, key):
    ctx = env.ctx(key)
    env.run(ctx, "plan")
    p = ctx.plan
    assert (
        p["release"].features and p["inventory"].screens and p["decision"].profile == PROFILE[key]
    )
    assert p["proposal"] and all(
        not r["differs_from_code"] for r in p["proposal"]
    )  # the mock keeps the code's decisions
    assert p["merges"] == [] and p["splits"] == {} and p["consents"] == []
    assert set(ctx.graph.features) == {r["feature"] for r in p["proposal"]}


def test_the_research_adapter_and_thin_research_are_handled(env):
    ctx = env.ctx("standard")
    ctx.research = research_adapter(None)  # an object without a view: reads as no research
    assert env.run(ctx).status == "awaiting_g2"


def test_a_code_only_run_has_no_runner_and_the_baseline_is_the_result(env, key):
    ctx = env.ctx(key)
    ctx.runner = None  # the zero-cost preview: no model call is possible
    assert env.run(ctx).status == "awaiting_g2"
    assert ctx.graph.canonical_text() == baseline(key).graph.canonical_text()
    assert ctx.plan["release"].features and ctx.facts == set(baseline(key).facts)
    assert ctx.rcode.changes is not None


def test_a_graph_derived_by_the_caller_is_kept_not_derived_again(env):
    a, b = env.ctx("standard"), env.ctx("standard")
    b.graph = baseline("standard").graph.model_copy(deep=True)  # the lead derived it already
    env.run(a)
    env.run(b)
    assert a.graph.canonical_text() == b.graph.canonical_text()
    assert b.plan[
        "release"
    ].features  # the plan data is rebuilt without touching the graph the caller gave


def test_the_model_never_writes_a_locked_node(env, key):
    base = baseline(key).graph
    ctx = env.ctx(key)
    env.run(ctx)
    g = ctx.graph
    for table in (
        "entities",
        "operations",
        "rules",
        "stories",
        "flows",
        "personas",
        "integrations",
    ):
        for nid, node in getattr(g, table).items():
            was = getattr(base, table).get(nid)
            if was is None:
                assert node.tier != "locked", (table, nid)  # a new node is never Locked
            else:
                assert node.tier == was.tier and node.aliases == was.aliases, (
                    table,
                    nid,
                )  # a rewritten one keeps its tier
                assert {(p.kind, p.ref) for p in was.provenance} <= {
                    (p.kind, p.ref) for p in node.provenance
                }
