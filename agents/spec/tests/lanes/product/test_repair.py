"""The failure ladder of the lane: ONE re-ask per node in the whole run, then the fallback with the node flagged degraded
and listed in ctx.flags; the plan has no fallback (PlanInvalid, the run ends partial, nothing is checkpointed)."""

from __future__ import annotations

import pytest

from mde_spec_agent.run.orchestrator import PlanInvalid
from mde_spec_agent.run.store import RunStore  # noqa: F401  (documents where checkpoints live)


def bad_story_feature(out):
    """An answer no code can mend: seven new fields on one entity (add only what a feature, story or fact needs)."""
    from mde_spec_agent.lanes.product.prod_module.schema import FieldRec

    out = out.model_copy(deep=True)
    ent = out.entities[0].id
    out.fields += [FieldRec(entity=ent, key=f"extra_{i}", type="text") for i in range(7)]
    return out


def test_a_bad_module_answer_is_reasked_once_and_the_second_answer_is_used(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        bad_story_feature
        if (spec == "spec.prod_module" and inp.module_id == "MOD-002" and n == 1)
        else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    results = env.calls_of(ctx, "spec.prod_module")
    reasked = [r for r in results if r.reasked]
    assert len(reasked) == 1 and not reasked[0].used_fallback and not reasked[0].flagged
    assert gw.count("spec.prod_module") == len(results) + 1  # one more gateway call: the re-ask
    assert ctx.flags == [] and not any(n.degraded for n in ctx.graph.all_nodes())
    assert not any(
        f.key.startswith("extra_") for e in ctx.graph.entities.values() for f in e.fields
    )


def test_a_second_failure_uses_the_fallback_flags_the_node_and_the_ledger_shows_one_reask(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        bad_story_feature if (spec == "spec.prod_module" and inp.module_id == "MOD-002") else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    degraded_calls = [r for r in env.calls_of(ctx, "spec.prod_module") if r.used_fallback]
    assert len(degraded_calls) == 1 and degraded_calls[0].flagged and degraded_calls[0].reasked
    ledger = ctx.runner.ledger
    assert ledger.reasked_nodes  # the nodes of that one batch
    assert all(c == 1 for c in ledger.used.values())  # exactly one re-ask per node, never two
    flagged_nodes = {f["node"] for f in ctx.flags}
    assert flagged_nodes and all(f["call"] == "spec.prod_module" for f in ctx.flags)
    degraded = {n.id for n in ctx.graph.all_nodes() if n.degraded}
    assert flagged_nodes <= degraded  # every flagged node is marked degraded in the graph
    # the fallback is the code-made draft, completed: still valid for the join
    from mde_spec_core.validators.base import blockers, run_all
    from mde_spec_core.validators.context import make_context

    assert [
        f.code for f in blockers(run_all(make_context(ctx.graph.model_copy(deep=True)), gate=1))
    ] == []


def test_the_validator_messages_reach_the_reask_word_for_word(env):
    """The second attempt carries the validator's own messages (here: a reference to an entity that does not exist)."""
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    seen: list[list[str]] = []

    def rewrite(spec, inp, n):
        if spec == "spec.prod_module" and inp.module_id == "MOD-002":
            seen.append(list(inp.reask))
            if n == 1:
                return bad_story_feature  # too many added fields: no code can choose which to keep
        return None

    gw.rewrite = rewrite
    assert env.run(ctx).status == "awaiting_g2"
    assert seen[0] == [] and seen[1], seen  # the second attempt carries the messages
    assert any("new fields" in m for m in seen[1])


def test_the_plan_is_invalid_twice_raises_plan_invalid_and_checkpoints_nothing(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)

    def drop_owner(out):
        out = out.model_copy(deep=True)
        out.owners = out.owners[1:]
        return out

    gw.rewrite = lambda spec, inp, n: drop_owner if spec == "spec.plan" else None
    out = env.run(ctx, "plan")
    assert out.status == "partial" and out.reason == "plan_invalid"
    assert gw.count("spec.plan") == 2  # the call and its one re-ask, nothing more
    assert ctx.store.get_run(ctx.run_id)["partial_reason"] == "plan_invalid"
    assert ctx.flags == [] and not ctx.plan.get("proposal")  # nothing applied
    # a resumed run retries it: the checkpoint of the plan batch does not exist
    gw.rewrite = None
    ctx.runner.ledger.used.clear()
    ctx.store.update_run(ctx.run_id, status="partial")
    assert env.run(ctx, "plan").status == "awaiting_g2"
    assert ctx.plan["proposal"]


def test_plan_invalid_is_raised_by_the_step_itself(env):
    import asyncio

    from mde_spec_agent.lanes.product import STEPS

    ctx = env.ctx("standard")
    env.gateway(ctx).rewrite = lambda spec, inp, n: (
        (lambda out: out.model_copy(update={"owners": []})) if spec == "spec.plan" else None
    )
    with pytest.raises(PlanInvalid):
        asyncio.run(STEPS["plan"](ctx))


def test_a_failed_fused_call_falls_back_to_one_call_per_part(env):
    ctx = env.ctx("quick")
    gw = env.gateway(ctx)

    def break_it(out):
        out = out.model_copy(deep=True)
        out.owners = []
        return out

    gw.rewrite = lambda spec, inp, n: break_it if spec == "spec.plan_fused" else None
    assert env.run(ctx).status == "awaiting_g2"
    assert ctx.plan["fused"] is False
    assert (
        gw.count("spec.plan_fused") == 2
        and gw.count("spec.plan") == 1
        and gw.count("spec.prod_module") >= 1
    )
    assert not any(
        n.degraded for n in ctx.graph.all_nodes()
    )  # the separate calls were fine: nothing degraded


def test_the_fusion_rule_says_no_when_the_writer_profile_has_no_output_limit(env):
    ctx = env.ctx("quick")
    ctx.writer = ctx.writer.model_copy(
        update={"max_output_tokens": None}
    )  # an honest unknown never fuses
    assert env.run(ctx).status == "awaiting_g2"
    gw = env.gateway(ctx)
    assert (
        ctx.plan["fused"] is False
        and gw.count("spec.plan_fused") == 0
        and gw.count("spec.plan") == 1
    )


def test_the_fusion_rule_says_no_when_the_fused_schema_does_not_fit_the_profile(env):
    ctx = env.ctx("quick")
    limits = ctx.writer.schema_limits.model_copy(update={"max_depth": 2})
    ctx.writer = ctx.writer.model_copy(update={"schema_limits": limits})
    assert env.run(ctx).status == "awaiting_g2"
    assert ctx.plan["fused"] is False and env.gateway(ctx).count("spec.plan_fused") == 0


def test_gate_1_runs_on_a_copy_of_the_graph_and_its_findings_become_validator_errors(env):
    """Gate 1 is the LAST validator of every call: a patch that breaks an edge (V-03) is reported against the call."""

    from mde_spec_agent.lanes.product.kit.batch import with_gate
    from mde_spec_agent.lanes.product.kit.gate import Minter
    from mde_spec_agent.lanes.product.prod_module import spec as module_spec
    from mde_spec_agent.lanes.product.prod_module.patch import build_module_patch
    from mde_spec_agent.lanes.product.steps.inputs import module_batches, module_input_factory
    from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of, gate_of

    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    gate = gate_of(ctx, e)
    b = next(x for x in module_batches(ctx, e) if x.module_id == "MOD-002")
    inp = module_input_factory(ctx, e)(b.module_id)(b.items)
    out = inp.baseline.model_copy(deep=True)
    assert gate.errors(build_module_patch(e, inp, out, Minter.for_batch(e.graph, 0), False)) == []

    def broken_build(
        i, o, m, f
    ):  # a patch with an edge to a screen that does not exist (code would have mended the answer)
        patch = build_module_patch(e, i, o, m, f)
        st = next(iter(patch["tables"]["stories"].values()))
        st["screen_ids"] = ["SCR-999"]
        return patch

    cs = with_gate(
        module_spec.SPEC,
        gate,
        lambda *a: broken_build(a[0], a[1], a[2], a[3]),
        Minter.for_batch(e.graph, 0),
    )
    errors = cs.validators[-1](inp, out)
    assert any("V-03" in m and "SCR-999" in m for m in errors), errors
    before = ctx.graph.canonical_text()
    assert ctx.graph.canonical_text() == before  # the real graph was never touched
    assert "SCR-999" not in before


# ---- what a real model does that the mock never did (found by the first live run) --------------------------------------


def invent_entities(out):
    from mde_spec_agent.lanes.product.prod_module.schema import EntityRec, FieldRec

    out = out.model_copy(deep=True)
    out.entities += [
        EntityRec(
            id="NEW-account", name="Account", klass="primary", prov=["assumed:sign-in needs it"]
        ),
        EntityRec(id="NEW-code", name="Code", klass="secondary", prov=["assumed:one-time codes"]),
        EntityRec(
            id="NEW-audit_event", name="Audit event", klass="log", prov=["assumed:a log of changes"]
        ),
    ]
    out.fields += [
        FieldRec(entity="NEW-account", key="email", type="email"),
        FieldRec(entity="NEW-code", key="value", type="code"),
        FieldRec(entity="NEW-audit_event", key="what", type="text"),
        FieldRec(
            entity=out.entities[0].id, key="owner_account", type="ref", ref_entity="NEW-account"
        ),
    ]
    return out


def test_an_invented_entity_that_is_not_join_or_log_is_dropped_by_code_not_failed(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        invent_entities if (spec == "spec.prod_module" and inp.module_id == "MOD-002") else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    results = env.calls_of(ctx, "spec.prod_module")
    assert not any(
        r.reasked or r.used_fallback for r in results
    )  # no re-ask, no fallback: the batch was not failed
    assert ctx.flags == [] and not any(n.degraded for n in ctx.graph.all_nodes())
    names = {e.name for e in ctx.graph.entities.values()}
    assert "Account" not in names and "Code" not in names  # dropped
    assert "Audit event" in names  # a log entity is allowed and kept
    assert all(f.key != "owner_account" for e in ctx.graph.entities.values() for f in e.fields)
    rows = [c for c in ctx.rcode.changes if "invented entity" in c.what]
    assert sorted(c.node_id for c in rows) == ["NEW-account", "NEW-code"]  # logged, never silent
    assert all(c.kind.value == "drop_unknown_optional" for c in rows)


def cite_research(out):
    out = out.model_copy(deep=True)
    for st in out.stories:
        st.prov = [*st.prov, "research:RI-001"]  # a story has no claims_used: V-36 would fail it
    out.entities[0].prov = [*out.entities[0].prov, "research:RI-001"]
    out.rules.append(
        __import__("mde_spec_agent.lanes.product.prod_module.schema", fromlist=["RuleRec"]).RuleRec(
            id="NEW-research_rule",
            subject="Sellers confirm",
            statement="A seller confirms each order",
            prov=["research:RI-001"],
        )
    )
    return out


def test_research_provenance_is_repaired_mechanically_so_v36_does_not_fail_the_batch(env):
    from mde_spec_core.validators.base import blockers, run_all
    from mde_spec_core.validators.context import make_context

    from .test_research import research_file

    ctx = env.ctx("standard", research=research_file())
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        cite_research if (spec == "spec.prod_module" and "RI-001" in inp.research_ids) else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_module"))
    g = ctx.graph
    assert [f.code for f in blockers(run_all(make_context(g.model_copy(deep=True)), gate=1))] == []
    stories = [s for s in g.stories.values() if any("RI-001" in p.ref for p in s.provenance)]
    assert stories and all(
        p.kind != "research" for s in stories for p in s.provenance
    )  # downgraded to assumed
    rule = next(r for r in g.rules.values() if r.subject == "Sellers confirm")
    assert rule.claims_used == [
        "RI-001"
    ]  # a rule can record its claim: kept, official evidence, Default
    assert any(c.what.startswith("research citation RI-001") for c in ctx.rcode.changes)


def test_a_single_source_claim_on_a_default_node_is_downgraded_not_left_to_fail():
    from mde_spec_agent.lanes.product.kit.common import prov_nodes, research_safe

    labels = {"RI-1": "official", "RI-2": "single_source", "RI-3": "unverified"}
    prov = prov_nodes(["research:RI-1", "research:RI-2", "research:RI-3", "user:FTR-001"])
    kept, claims, rows = research_safe(prov, labels, claims_field=True, tier="default")
    assert claims == ["RI-1"] and len(rows) == 2 and [p.kind for p in kept].count("research") == 1
    kept, claims, _ = research_safe(prov, labels, claims_field=True, tier="open")
    assert claims == ["RI-1", "RI-2"]  # an Open node may rest on a single source
    kept, claims, _ = research_safe(prov, labels, claims_field=False)
    assert claims == [] and all(p.kind != "research" for p in kept)


# ---- provenance the call did not offer: repaired by code, never a re-ask (second live run) ---------------------------------


def test_repair_prov_rewrites_a_named_node_drops_the_unknown_and_never_invents_evidence(env):
    from mde_spec_agent.lanes.product.kit.common import ProvAllowed, repair_prov
    from mde_spec_agent.lanes.product.steps.inputs import module_batches, module_input_factory
    from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of

    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    b = module_batches(ctx, e)[0]
    allowed = ProvAllowed.of(module_input_factory(ctx, e)(b.module_id)(b.items))
    rule = next(r for r in e.graph.rules.values() if r.provenance)
    flow = next(iter(e.graph.flows))
    own = [f"{p.kind}:{p.ref}" for p in rule.provenance]
    offered = "user:FTR-001"
    assert offered.split(":")[1] in allowed.refs
    kept, rows = repair_prov(
        [
            offered,
            f"compass:{rule.id}",
            f"policy:brief-{flow}",
            "user:FTR-999",
            "gossip:nobody",
            "research:RI-9",
        ],
        allowed,
        e.graph,
        "X",
    )
    assert offered in kept
    assert all(
        o in kept for o in own
    )  # a draft rule cited as a source: rewritten to what that rule rests on
    assert not any(
        x.endswith(("FTR-999", "nobody", "RI-9")) for x in kept
    )  # unknown: dropped, never invented
    assert len(rows) == 5 and {r["kind"] for r in rows} == {
        "exact_synonym",
        "drop_unknown_optional",
    }
    # nothing left: the node is marked assumed rather than left without a source
    only, _ = repair_prov(["user:FTR-999"], allowed, e.graph, "X")
    assert only == ["assumed:no source the call offered"]


def echo_bad_ids(out):
    out = out.model_copy(deep=True)
    for r in out.rules:
        r.prov = [f"compass:{r.id}"]  # the draft rule's own id as a compass source
    if out.stories:
        out.stories[0].prov = ["user:FTR-999", "policy:brief-FLW-001"]
    if out.entities:
        out.entities[0].prov = []
    return out


def test_a_module_answer_with_unoffered_ids_is_repaired_not_reasked(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        echo_bad_ids if (spec == "spec.prod_module" and inp.baseline.rules) else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    results = env.calls_of(ctx, "spec.prod_module")
    assert not any(r.reasked or r.used_fallback for r in results) and ctx.flags == []
    g = ctx.graph
    assert all(
        p.ref != r.id for r in g.rules.values() for p in r.provenance
    )  # no self-citation left
    assert any(
        "not a source this call offered" in c.what for c in ctx.rcode.changes
    )  # each change is logged


def echo_bad_flow_prov(out):
    out = out.model_copy(deep=True)
    for f in out.flows:
        f.prov = [f"policy:brief-{f.id}"]
    return out


def test_a_journeys_answer_that_cites_a_flow_as_a_policy_is_repaired_not_reasked(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: echo_bad_flow_prov if spec == "spec.prod_journeys" else None
    assert env.run(ctx).status == "awaiting_g2"
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_journeys"))
    assert ctx.flags == []
    assert not any(
        p.kind == "policy" and "brief-" in p.ref
        for f in ctx.graph.flows.values()
        for p in f.provenance
    )


def test_an_assumed_story_that_says_must_is_made_usually_by_code(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)

    def assumed_must(out):
        out = out.model_copy(deep=True)
        st = out.stories[0].model_copy(
            update={"id": "NEW-x", "voice": "must", "prov": ["assumed:no source"]}
        )
        out.stories.append(st)
        out.acceptance.append(out.acceptance[0].model_copy(update={"story": "NEW-x"}))
        return out

    gw.rewrite = lambda spec, inp, n: (
        assumed_must if (spec == "spec.prod_module" and inp.baseline.stories and n == 1) else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_module"))
    new = [s for s in ctx.graph.stories.values() if s.assumed]
    assert new and all(s.voice != "must" for s in new)


# ---- a story the model added that cites no in feature (live run 5) ----------------------------------------------------------


def _story(key, want, features=()):
    from mde_spec_agent.lanes.product.prod_module.schema import AcceptRec, StoryRec

    st = StoryRec(
        id=f"NEW-{key}", role="ROL-001", want=want, so_that="the person gets it done",
        feature_ids=list(features), voice="usually", prov=["assumed:not in the draft"],
    )  # fmt: skip
    return st, AcceptRec(story=st.id, given="g", when="w", then="t")


def test_a_model_added_story_with_no_in_feature_is_attached_by_name_by_module_or_dropped(env):
    from mde_spec_agent.lanes.product.prod_module.repair import repair
    from mde_spec_agent.lanes.product.prod_module.validate import validate

    from .test_validators import module_case

    inp, out = module_case(env)
    inp = inp.model_copy(
        update={
            "in_feature_ids": ["FTR-001", "FTR-002", "FTR-003"],
            "feature_ids": ["FTR-001", "FTR-002", "FTR-003", "FTR-004"],
            "feature_names": ["FTR-001=Class booking", "FTR-002=Language settings", "FTR-003=Weekly timetable"],
            "module_feature_ids": ["FTR-001", "FTR-002"],
        }
    )  # fmt: skip
    out = out.model_copy(deep=True)
    for key, want in [
        ("switch_language", "switch the language"),
        ("book_two", "book a class for a friend"),
    ]:
        st, acc = _story(key, want)
        out.stories.append(st)
        out.acceptance.append(acc)
    fixed, rows = repair(inp, out)
    by_id = {s.id: s for s in fixed.stories}
    assert by_id["NEW-switch_language"].feature_ids == ["FTR-002"]  # one feature fits by name
    assert by_id["NEW-book_two"].feature_ids == ["FTR-001"]  # one fits by name too (booking)
    assert all(
        any(p.startswith("assumed:attached to feature") for p in by_id[k].prov)
        for k in by_id
        if k.startswith("NEW-")
    )
    attach = [r for r in rows if "cites no in feature" in r["what"]]
    assert len(attach) == 2
    # none fits by name and the module serves two features: dropped (with its acceptance), never left without a feature
    st, acc = _story("something_else", "do something unrelated")
    gone, rows = repair(
        inp,
        out.model_copy(
            update={"stories": [*out.stories, st], "acceptance": [*out.acceptance, acc]}
        ),
    )
    assert "NEW-something_else" not in {s.id for s in gone.stories} and all(
        a.story != st.id for a in gone.acceptance
    )
    assert any(
        r["kind"] == "drop_unknown_optional" and r["node_id"] == "NEW-something_else" for r in rows
    )
    # a module that serves exactly one feature attaches it
    one = inp.model_copy(update={"module_feature_ids": ["FTR-003"]})
    kept, _ = repair(
        one,
        out.model_copy(
            update={"stories": [*out.stories, st], "acceptance": [*out.acceptance, acc]}
        ),
    )
    assert next(s for s in kept.stories if s.id == st.id).feature_ids == ["FTR-003"]
    # the validator accepts all of it (nothing to re-ask)
    assert [m for m in validate(inp, out) if "cite an in feature" in m and "NEW-" in m] == []
    st2, _ = _story(
        "bad", "x", features=["FTR-999"]
    )  # an unknown feature id is dropped, then the story is attached or dropped
    assert [
        m
        for m in validate(inp, out.model_copy(update={"stories": [*out.stories, st2]}))
        if "NEW-" in m
    ] == []


def add_unattached_stories(out):
    out = out.model_copy(deep=True)
    base_features = {f for s in out.stories for f in s.feature_ids}
    for key in ("switch_language", "sign_in_phone"):
        st, acc = _story(key, f"{key.replace('_', ' ')} for the person")
        out.stories.append(st)
        out.acceptance.append(acc)
    assert base_features
    return out


def test_unattached_added_stories_are_repaired_without_a_reask_and_v40_stays_whole(env):
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    ctx = env.ctx("standard")
    env.gateway(ctx).rewrite = lambda spec, inp, n: (
        add_unattached_stories if (spec == "spec.prod_module" and inp.baseline.stories) else None
    )
    out = env.run(ctx)
    assert out.status == "awaiting_g2", out
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_module"))
    assert ctx.flags == []
    g = ctx.graph
    assert any(
        "cites no in feature and no policy id" in c.what for c in ctx.rcode.changes
    )  # logged
    v40 = [
        f
        for f in run_all(make_context(g.model_copy(deep=True)), gate=3, only={"V-40", "V-19"})
        if f.severity == "blocker"
    ]
    assert v40 == []  # a story with no feature never reaches the graph
    for st in g.stories.values():
        assert st.policy_id or any(
            f in g.features and g.features[f].status == "in" for f in st.feature_ids
        )


# ---- first live Thorough run: what fell back there is mended by code now ---------------------------------------------------


def _damage_journeys(out):
    out = out.model_copy(deep=True)
    for f in out.flows:
        if not f.id.startswith("NEW-"):
            f.unhappy = []  # the draft flow's own alternates dropped
    if len(out.flows) > 1:
        victim = out.flows[-1].id
        out.stages = [s for s in out.stages if s.flow != victim]  # "write its stages"
    return out


def test_journeys_that_drop_the_alternates_or_the_stages_are_mended_not_reasked(env):
    ctx = env.ctx("thorough")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: _damage_journeys if spec == "spec.prod_journeys" else None
    out = env.run(ctx)
    assert out.status == "awaiting_g2", out
    results = env.calls_of(ctx, "spec.prod_journeys")
    assert results and not any(r.reasked or r.used_fallback for r in results)
    assert ctx.flags == []
    g = ctx.graph
    assert all(f.stages for f in g.flows.values())
    from .conftest import baseline

    for fid, f in baseline("thorough").graph.flows.items():
        assert (
            g.flows[fid].unhappy_paths[: len(f.unhappy_paths)] == f.unhappy_paths
        )  # the flow's own alternates, first
    assert any(
        "alternates were dropped" in c.what or "no stages" in c.what for c in ctx.rcode.changes
    )


def _damage_module(out):
    from mde_spec_agent.lanes.product.prod_module.schema import RuleRec

    out = out.model_copy(deep=True)
    ent = next(
        (e.entity for e in out.states if sum(1 for x in out.states if x.entity == e.entity) >= 2),
        None,
    )
    if ent is not None:  # every status has a way out: no terminal status
        names = [s.state for s in out.states if s.entity == ent]
        roles = next((t.roles for t in out.transitions if t.entity == ent), ["ROL-001"])
        out.transitions = [t for t in out.transitions if t.entity != ent] + [
            type(out.transitions[0])(entity=ent, from_state=a, to_state=b, roles=list(roles))
            for a, b in zip(names, [*names[1:], names[0]], strict=True)
        ]
    out.rules.append(
        RuleRec(
            id="NEW-limit",
            subject="Limit",
            statement="The limit applies",
            value="7357",
            unit="items",
            prov=["user:FTR-001"],
        )
    )
    if out.acceptance:
        out.acceptance[0] = out.acceptance[0].model_copy(
            update={"then": "the person sees two quotes on the left and the right and chooses one"}
        )
    return out


def test_module_answers_with_no_terminal_status_an_invented_value_or_a_layout_clause_are_mended(
    env,
):
    ctx = env.ctx("thorough")
    gw = env.gateway(ctx)
    gw.rewrite = lambda spec, inp, n: (
        _damage_module if spec == "spec.prod_module" and inp.baseline.stories else None
    )
    out = env.run(ctx)
    assert out.status == "awaiting_g2", out
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_module"))
    assert ctx.flags == []
    from mde_spec_core.validators.base import blockers, run_all
    from mde_spec_core.validators.context import make_context

    g = ctx.graph
    assert (
        list(
            blockers(
                run_all(
                    make_context(g.model_copy(deep=True)),
                    gate=3,
                    only={"V-09", "V-10", "V-40", "V-45"},
                )
            )
        )
        == []
    )
    assert all(
        r.value is None for r in g.rules.values() if r.subject == "Limit"
    )  # the value no fact states is gone
    assert all("on the left" not in a.then for st in g.stories.values() for a in st.acceptance)
    kinds = " ".join(c.what for c in ctx.rcode.changes)
    assert "lifecycle" in kinds and "a value no fact states" in kinds and "layout word" in kinds


def test_a_draft_rule_echoed_with_another_number_is_not_judged_and_the_drafts_text_stays(env):
    from mde_spec_agent.lanes.product.prod_module.validate import validate

    from .test_validators import module_case

    inp, out = module_case(env)
    assert out.rules  # the produce export has rules in this module
    out = out.model_copy(deep=True)
    out.rules[0].value, out.rules[0].unit, out.rules[0].statement = (
        "9999",
        "days",
        "Under 9999 days applies",
    )
    assert validate(inp, out) == []  # the live run fell back on exactly this


def _ops_on_other_entities(out):
    from mde_spec_agent.lanes.product.prod_module.schema import OperationRec, OpErrorRec

    out = out.model_copy(deep=True)
    for key, ent in (("trk-list-tracked", "ENT-004"), ("trk-delivery-proof", "ENT-008")):
        out.operations.append(
            OperationRec(
                id=f"NEW-{key}",
                name=key.replace("-", " "),
                entity=ent,
                method="POST",
                path=f"/{key}",
                roles=["ROL-001"],
                prov=["assumed:x"],
            )  # fmt: skip
        )
        out.op_errors.append(OpErrorRec(op=f"NEW-{key}", code="forbidden", recovery="stay"))
    return out


def test_operations_on_an_entity_of_another_module_are_relinked_or_dropped_not_reasked(env):
    ctx = env.ctx("thorough")
    env.gateway(ctx).rewrite = lambda spec, inp, n: (
        _ops_on_other_entities if spec == "spec.prod_module" and inp.baseline.entities else None
    )
    out = env.run(ctx)
    assert out.status == "awaiting_g2", out
    assert not any(r.reasked or r.used_fallback for r in env.calls_of(ctx, "spec.prod_module"))
    assert ctx.flags == []
    for o in ctx.graph.operations.values():
        assert o.entity is None or o.entity in ctx.graph.entities
        assert set(o.roles) <= set(ctx.graph.roles)
    assert any(
        "entity ENT-00" in c.what and "not in this call" in c.what for c in ctx.rcode.changes
    )
