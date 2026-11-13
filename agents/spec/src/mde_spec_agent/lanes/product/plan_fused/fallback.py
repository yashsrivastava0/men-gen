"""Fallback of the fused call: every part's own fallback, joined. The step does not trust a fused fallback (the plan has no
safe default): it goes back to the one-call-per-part form instead."""

from __future__ import annotations

from mde_spec_agent.lanes.product.plan.fallback import fallback as plan_fb
from mde_spec_agent.lanes.product.prod_integ.fallback import fallback as integ_fb
from mde_spec_agent.lanes.product.prod_journeys.fallback import fallback as journeys_fb
from mde_spec_agent.lanes.product.prod_module.fallback import fallback as module_fb

from .schema import FusedIn, FusedOut, join_parts


def fallback(inp: FusedIn) -> FusedOut:
    return join_parts(
        plan_fb(inp.plan), module_fb(inp.module), integ_fb(inp.integ), journeys_fb(inp.journeys)
    )
