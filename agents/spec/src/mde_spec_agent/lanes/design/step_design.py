"""The `design` step: one DSGN call per run (all candidates and the unbound needs), then tokens by code.

Runs in PARALLEL with the Product lane right after the plan, so it reads only the baseline graph and the SpecInput.
It does not write signature moments (they need the frozen journeys: SCRN writes them)."""

from __future__ import annotations

import dataclasses
from typing import Any

from mde_spec_agent.calls.base import RunResult, Truncated
from mde_spec_agent.lanes.design._gate import gate1_messages
from mde_spec_agent.lanes.design._versions import versions_of
from mde_spec_agent.lanes.design.brief import BrandFacts, build_dsgn_input
from mde_spec_agent.lanes.design.build import build_patch
from mde_spec_agent.lanes.design.dsgn._node import direction_id
from mde_spec_agent.lanes.design.dsgn.repair import repair
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut
from mde_spec_agent.lanes.design.dsgn.spec import SPEC
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.callkit.ladder import FixKind

STEP = "design"


def _fallback_result(
    inp: DsgnIn, reason: str, prior: RunResult[DsgnOut] | None = None
) -> RunResult[DsgnOut]:
    if prior is not None:
        return dataclasses.replace(
            prior, output=SPEC.fallback(inp), used_fallback=True, flagged=True, reason=reason
        )
    return RunResult(output=SPEC.fallback(inp), used_fallback=True, flagged=True, reason=reason)


async def run_checked(
    ctx: RunContext, inp: DsgnIn, brand: BrandFacts, ids: list[str]
) -> tuple[RunResult[DsgnOut], Patch]:
    """The call, then Gate 1 on a COPY of the graph. A finding about a node this call wrote is ONE targeted re-ask (the
    runner's ledger allows one per node in the whole run), then the deterministic fallback: the lane never blocks."""

    def patch_for(res: RunResult[DsgnOut]) -> Patch:
        out, fixes = repair(inp, res.output)  # mechanical repairs: logged, never a re-ask
        patch = build_patch(
            ctx.graph,
            inp,
            out,
            brand,
            ctx.config,
            flagged=res.flagged,
            from_fallback=res.used_fallback,
            reason=res.reason,
        )
        patch["rcode"] = [f.as_row() for f in fixes]
        return patch

    runner = ctx.runner
    if runner is None:  # a code-only run: no model, the deterministic directions, said out loud
        res = _fallback_result(inp, "no model route in a code-only run")
        return res, patch_for(res)
    try:
        res = await runner.run(SPEC, inp, node_ids=ids, step=STEP)
    except Truncated:
        res = _fallback_result(inp, "the answer was cut off; deterministic directions used")
        return res, patch_for(res)
    patch = patch_for(res)
    if res.used_fallback:
        return res, patch
    errors = gate1_messages(ctx.graph, patch, ctx.config)
    if not errors:
        return res, patch
    if not any(runner.ledger.has_used(n) for n in ids):
        for n in ids:
            runner.ledger.mark(n)
        try:
            res2 = await runner.run(
                SPEC, inp.model_copy(update={"reask": errors[:20]}), node_ids=(), step=STEP
            )
        except Truncated:
            res2 = _fallback_result(inp, "the answer was cut off")
        if not res2.used_fallback:
            patch2 = patch_for(res2)
            if not gate1_messages(ctx.graph, patch2, ctx.config):
                return dataclasses.replace(res2, reasked=True), patch2
    res = _fallback_result(inp, "gate 1: " + "; ".join(errors)[:240], res)
    return res, patch_for(res)


async def design(ctx: RunContext) -> None:
    inp, brand = build_dsgn_input(
        ctx.graph, ctx.spec_input, ctx.config, ctx.program_id, ctx.input_hash
    )
    ids = [direction_id(i) for i in range(inp.count)]
    summary: dict[str, Any] = {}

    async def produce() -> Patch:
        res, patch = await run_checked(ctx, inp, brand, ids)
        summary.update(fallback=res.used_fallback, reasked=res.reasked)
        return patch

    patch = await ctx.batch(STEP, "directions", versions_of(ctx.versions, SPEC), produce)
    ctx.apply(patch)
    ctx.flags.extend(patch.get("flags", []))
    for r in patch.get("rcode", []):  # logged on a fresh run and on a replay alike
        ctx.rcode.record(FixKind(r["kind"]), r["node_id"], r["what"], r["before"], r["after"])
