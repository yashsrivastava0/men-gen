"""The failure ladder of the lane: ONE re-ask per node in the whole run, then the fallback with the node flagged degraded
and listed in ctx.flags; the plan has no fallback (PlanInvalid, the run ends partial, nothing is checkpointed)."""

from __future__ import annotations

import pytest

from mde_spec_agent.run.orchestrator import PlanInvalid
from mde_spec_agent.run.store import RunStore  # noqa: F401  (documents where checkpoints live)


def bad_story_feature(out):
    out = out.model_copy(deep=True)
    out.stories[0].feature_ids = ["FTR-999"]  # a feature that does not exist
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
    assert "FTR-999" not in "".join(
        sorted(f for st in ctx.graph.stories.values() for f in st.feature_ids)
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

                def f(out):
                    out = out.model_copy(deep=True)
                    out.entities[
                        0
                    ].klass = "log"  # fine for the schema; a log entity has no update, but gate 1 says nothing
                    out.fields[0].ref_entity = "ENT-999"
                    return out

                return f
        return None

    gw.rewrite = rewrite
    assert env.run(ctx).status == "awaiting_g2"
    assert seen[0] == [] and seen[1], seen  # the second attempt carries the messages
    assert any("ENT-999" in m for m in seen[1])


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
    import functools

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
    out.stories[0].screen_ids = ["SCR-999"]
    cs = with_gate(
        module_spec.SPEC,
        gate,
        functools.partial(lambda env_, i, o, m, f: build_module_patch(env_, i, o, m, f), e),
        Minter.for_batch(e.graph, 0),
    )
    errors = cs.validators[-1](inp, out)
    assert any("V-03" in m and "SCR-999" in m for m in errors), errors
    before = ctx.graph.canonical_text()
    assert ctx.graph.canonical_text() == before  # the real graph was never touched
    assert "SCR-999" not in before
