"""Checks of the fused answer: no merges, no splits, no new entity; then every part's own checks on its part."""

from __future__ import annotations

from mde_spec_agent.lanes.product.plan.validate import validate as validate_plan
from mde_spec_agent.lanes.product.prod_integ.validate import validate as validate_integ
from mde_spec_agent.lanes.product.prod_journeys.validate import validate as validate_journeys
from mde_spec_agent.lanes.product.prod_module.repair import repair
from mde_spec_agent.lanes.product.prod_module.validate import validate as validate_module

from .schema import FusedIn, FusedOut, parts_of


def validate(inp: FusedIn, out: FusedOut) -> list[str]:
    errors: list[str] = []
    if any(
        e.id.startswith("NEW-") for e in repair(parts_of(out)[1])[0].entities
    ):  # invented non-join entities are dropped by code
        errors.append("a fused answer adds no new entity; use the draft's entities")
    plan, module, integ, journeys = parts_of(out)
    errors += validate_plan(inp.plan, plan)
    errors += validate_module(inp.module, module)
    errors += validate_integ(inp.integ, integ)
    errors += validate_journeys(inp.journeys, journeys)
    return errors[:20]
