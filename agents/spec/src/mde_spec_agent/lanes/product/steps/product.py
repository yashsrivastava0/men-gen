"""The `product` step: the module calls (one per module, or per part of a big module), the integration calls and the
journey calls. They are independent: one `ctx.batch` per call batch, all gathered side by side, the patches applied in
sorted batch order afterwards (identical bytes on a resume). Gate 1 runs after every call; a call that ends flagged marks
its nodes degraded and appends to `ctx.flags` (listed at G2). A compact app whose plan call also wrote the product skips
this step's calls (nothing is written twice)."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

from mde_spec_agent.lanes.product.kit.batch import (
    Attempt,
    apply_patches,
    gather_batches,
    run_batch,
    versions_of,
)
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_agent.lanes.product.prod_integ import spec as integ_spec
from mde_spec_agent.lanes.product.prod_integ.patch import build_integ_patch
from mde_spec_agent.lanes.product.prod_journeys import spec as journeys_spec
from mde_spec_agent.lanes.product.prod_journeys.patch import build_journeys_patch
from mde_spec_agent.lanes.product.prod_module import spec as module_spec
from mde_spec_agent.lanes.product.prod_module.patch import build_module_patch
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.validators.base import run_all
from mde_spec_core.validators.context import make_context

from .inputs import (
    integ_batches,
    integ_input_factory,
    journey_batches,
    journeys_input_factory,
    module_batches,
    module_input_factory,
)
from .plan import env_of, fused_succeeded, gate_of

STEP = "product"


def _build(builder: Any, env: Any, inp: Any, out: Any, minter: Minter, flagged: bool) -> Patch:
    return builder(env, inp, out, minter, flagged)


def _module_nodes(items: Sequence[tuple[str, str]]) -> list[str]:
    """The nodes a re-ask of a module batch is about: its entities, stories and rules (or its screens)."""
    return [i for k, i in items if k in ("E", "S", "R")] or [i for _, i in items]


def _plain_nodes(items: Sequence[str]) -> list[str]:
    return list(items)


async def product(ctx: RunContext) -> None:
    if ctx.runner is None:  # a code-only run: the baseline graph is the product
        return
    if fused_succeeded(ctx):
        ctx.emit("step.finished", step=STEP, note="written by the fused plan call")
        return
    env = env_of(ctx)
    gate = gate_of(ctx, env)

    # the whole batch plan first (sorted ids), so every batch has a fixed id range whatever order they finish in
    plans: list[tuple[str, str, Any, Any]] = []  # (batch id, kind, items, extra)
    for b in module_batches(ctx, env):
        plans.append((b.batch_id, "module", b.items, b.module_id))
    for bid, ids in integ_batches(ctx, env):
        plans.append((bid, "integ", ids, None))
    for bid, ids in journey_batches(ctx, env):
        plans.append((bid, "journeys", ids, None))
    plans.sort(key=lambda p: p[0])

    make_module = module_input_factory(ctx, env)
    make_integ = integ_input_factory(ctx, env)
    make_journeys = journeys_input_factory(ctx, env)

    coros = []
    for ordinal, (bid, kind, items, extra) in enumerate(plans):
        minter = Minter.for_batch(env.graph, ordinal)
        if kind == "module":
            cs, builder = module_spec.SPEC, build_module_patch
            make = make_module(extra)
            node_ids = _module_nodes
            pv, sv = module_spec.PROMPT_VERSION, module_spec.SCHEMA_VERSION
        elif kind == "integ":
            cs, builder = integ_spec.SPEC, build_integ_patch
            make = make_integ
            node_ids = _plain_nodes
            pv, sv = integ_spec.PROMPT_VERSION, integ_spec.SCHEMA_VERSION
        else:
            cs, builder = journeys_spec.SPEC, build_journeys_patch
            make = make_journeys
            node_ids = _plain_nodes
            pv, sv = journeys_spec.PROMPT_VERSION, journeys_spec.SCHEMA_VERSION
        attempt: Attempt[Any] = Attempt(
            ctx=ctx,
            cs=cs,
            step=STEP,
            gate=gate,
            make_input=make,
            build=functools.partial(_build, builder, env),
            node_ids=node_ids,
            minter=minter,
        )
        coros.append(run_batch(ctx, STEP, bid, versions_of(ctx, cs, pv, sv), attempt, items))
    patches = await gather_batches(coros)

    ordered = [(bid, p) for (bid, *_), p in zip(plans, patches, strict=True)]
    apply_patches(ctx, ordered)
    reasked = sum(1 for _, p in ordered if p.get("reasked"))
    if reasked:
        ctx.emit("repair.reask", step=STEP, batches=reasked)
    findings = run_all(make_context(ctx.graph.model_copy(deep=True)), gate=1)
    ctx.emit(
        "validators.done",
        step=STEP,
        gate=1,
        blockers=sum(1 for f in findings if f.severity == "blocker"),
        warnings=sum(1 for f in findings if f.severity == "warning"),
        batches=len(plans),
        degraded=len(ctx.flags),
    )
