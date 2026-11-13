"""Resume determinism and truncation: a killed batch resumes to the same canonical graph text as an uninterrupted run;
a truncated call is redone in smaller batches and yields the same nodes."""

from __future__ import annotations

import json

import pytest

from mde_core.errors import ProviderError

from .conftest import is_compact


def test_a_killed_batch_resumes_to_the_same_bytes(env, key):
    ref = env.ctx(key)
    assert env.run(ref).status == "awaiting_g2"
    reference = ref.graph.canonical_text()

    ctx = env.ctx(key)
    gw = env.gateway(ctx)
    target = {"done": False}

    def boom(spec, inp, n):
        # kill the SECOND product-step call batch (a bug-like crash, not a model failure): the others have checkpointed
        if (
            spec in ("spec.prod_module", "spec.prod_journeys", "spec.prod_integ")
            and not target["done"]
        ):
            target["count"] = target.get("count", 0) + 1
            if target["count"] == 2:
                target["done"] = True
                return RuntimeError("process killed")
        return None

    gw.raise_on = boom
    out = env.run(ctx)
    if is_compact(key):  # the fused call is one batch: kill it instead (nothing finished yet)
        assert out.status == "awaiting_g2"
        return
    assert out.status == "failed" and out.stage == "product"
    ctx.store.update_run(ctx.run_id, status="partial")  # the operator marks it resumable
    ctx.plan.clear()  # a new process has no ctx.plan: the steps rebuild what they need
    ctx.flags.clear()
    before = (
        gw.count("spec.prod_module") + gw.count("spec.prod_journeys") + gw.count("spec.prod_integ")
    )
    out = env.run(ctx)
    assert out.status == "awaiting_g2"
    assert ctx.graph.canonical_text() == reference
    after = (
        gw.count("spec.prod_module") + gw.count("spec.prod_journeys") + gw.count("spec.prod_integ")
    )
    assert 0 < after - before < before  # only the unfinished batches were called again


def test_the_fused_call_resumes_too(env):
    ref = env.ctx("quick")
    env.run(ref)
    ctx = env.ctx("quick")
    gw = env.gateway(ctx)
    gw.raise_on = lambda spec, inp, n: (
        RuntimeError("process killed") if spec == "spec.plan_fused" and n == 1 else None
    )
    assert env.run(ctx).status == "failed"
    gw.raise_on = None
    ctx.store.update_run(ctx.run_id, status="partial")
    ctx.plan.clear()
    assert env.run(ctx).status == "awaiting_g2"
    assert ctx.graph.canonical_text() == ref.graph.canonical_text()


def test_a_resumed_run_replays_checkpoints_instead_of_calling_again(env):
    ctx = env.ctx("standard")
    env.run(ctx)
    gw = env.gateway(ctx)
    n = len(gw.calls)
    # run both stages again on the same run: every batch is a checkpoint hit, nothing is called
    ctx.store.update_run(ctx.run_id, status="partial", done_steps=[])
    ctx.plan.clear()
    ctx.graph = type(ctx.graph)()
    assert env.run(ctx).status == "awaiting_g2"
    assert len(gw.calls) == n


@pytest.mark.parametrize("limit", [1, 2])
def test_a_truncated_module_batch_is_redone_in_smaller_batches_with_the_same_nodes(env, limit):
    ref = env.ctx("thorough")
    env.run(ref)

    ctx = env.ctx("thorough")
    gw = env.gateway(ctx)
    gw.truncate_over = {
        "spec.prod_module": limit
    }  # the provider cannot finish more than `limit` items in one answer
    assert env.run(ctx).status == "awaiting_g2"
    calls = [(s, i) for s, i, _ in gw.calls if s == "spec.prod_module"]
    assert max(i for _, i in calls if i <= limit) <= limit
    assert gw.count("spec.prod_module") > ref_calls(env, ref)  # more, smaller calls
    assert ctx.flags == []  # smaller batches fit: nothing degraded
    assert _nodes(ctx) == _nodes(ref)  # the same nodes, whatever the batch size


def ref_calls(env, ref) -> int:
    return len(env.calls_of(ref, "spec.prod_module"))


def _nodes(ctx):
    g = ctx.graph
    return {
        "entities": {
            k: v.model_dump(mode="json", exclude={"input_hash"}) for k, v in g.entities.items()
        },
        "stories": {
            k: v.model_dump(mode="json", exclude={"input_hash"}) for k, v in g.stories.items()
        },
        "rules": {k: v.model_dump(mode="json", exclude={"input_hash"}) for k, v in g.rules.items()},
        "ops": sorted((o.name, o.method, o.path, tuple(o.roles)) for o in g.operations.values()),
    }


def test_a_truncated_batch_of_three_items_yields_the_same_nodes_via_smaller_batches(env):
    ref = env.ctx("thorough")
    env.run(ref)
    ctx = env.ctx("thorough")
    gw = env.gateway(ctx)
    gw.truncate_over = {"spec.prod_module": 2}
    env.run(ctx)
    sizes = sorted({i for s, i, _ in gw.calls if s == "spec.prod_module"})
    assert any(i >= 3 for i in sizes)  # there were batches of three or more that truncated
    assert _nodes(ctx) == _nodes(ref)


def test_an_item_that_still_truncates_alone_takes_its_fallback_and_is_flagged(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.truncate_over = {"spec.prod_module": 0}  # nothing fits: every single item truncates
    assert env.run(ctx).status == "awaiting_g2"
    assert ctx.flags and all(f["reason"] == "truncated" for f in ctx.flags)
    assert {f["node"] for f in ctx.flags} <= {n.id for n in ctx.graph.all_nodes() if n.degraded}


def test_provider_errors_other_than_truncation_use_the_reask_then_the_fallback(env):
    ctx = env.ctx("standard")
    gw = env.gateway(ctx)
    gw.raise_on = lambda spec, inp, n: (
        ProviderError("scripted provider failure") if spec == "spec.prod_integ" else None
    )
    assert env.run(ctx).status == "awaiting_g2"
    assert gw.count("spec.prod_integ") == 2 * len(
        env.calls_of(ctx, "spec.prod_integ")
    )  # call + its one re-ask
    assert any(f["call"] == "spec.prod_integ" for f in ctx.flags)
    ints = [i for i in ctx.graph.integrations.values() if i.provider_side_flow]
    assert ints and all(i.interruption and i.pending_timeout and i.degraded for i in ints)


def test_the_stored_checkpoints_are_plain_json(env):
    ctx = env.ctx("standard")
    env.run(ctx)
    runs = ctx.store.run_dir(ctx.run_id)
    files = list(runs.rglob("*.json"))
    assert files
    for p in files:
        json.loads(p.read_text(encoding="utf-8"))
