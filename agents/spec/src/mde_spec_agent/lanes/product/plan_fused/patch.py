"""The patch of a fused answer: the plan part, then the product parts over the graph the plan made (a fused answer has no
merges and no splits, so the product parts apply to the same ids). All tables are merged; later parts win a shared id."""

from __future__ import annotations

from mde_spec_agent.lanes.product.kit.env import Env
from mde_spec_agent.lanes.product.kit.gate import Minter, apply_ext
from mde_spec_agent.lanes.product.plan.patch import build_plan_patch
from mde_spec_agent.lanes.product.prod_integ.patch import build_integ_patch
from mde_spec_agent.lanes.product.prod_journeys.patch import build_journeys_patch
from mde_spec_agent.lanes.product.prod_module.patch import build_module_patch
from mde_spec_agent.run.patch import Patch

from .schema import FusedIn, FusedOut, parts_of


def build_fused_patch(
    env: Env, inp: FusedIn, out: FusedOut, minter: Minter, flagged: bool
) -> Patch:
    plan, module, integ, journeys = parts_of(out)
    plan_patch = build_plan_patch(env, inp.plan, plan, minter, flagged)
    after = env.graph.model_copy(
        deep=True
    )  # the product parts are written over the graph the plan made
    apply_ext(after, plan_patch)
    env2 = Env(after, env.spec_input, env.maps)
    parts = [
        plan_patch,
        build_module_patch(env2, inp.module, module, minter, flagged),
        build_integ_patch(env2, inp.integ, integ, minter, flagged),
        build_journeys_patch(env2, inp.journeys, journeys, minter, flagged),
    ]
    tables: dict[str, dict] = {}
    for p in parts:
        for t, nodes in p.get("tables", {}).items():
            tables.setdefault(t, {}).update(nodes)
    merged: Patch = {"tables": tables, "replace": True, "plan": parts[0].get("plan", {})}
    for k in ("remove", "locale_copy"):
        if parts[0].get(k):
            merged[k] = parts[0][k]
    return merged
