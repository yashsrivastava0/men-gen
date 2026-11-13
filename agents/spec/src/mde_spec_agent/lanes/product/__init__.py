"""The product lane: its call specs (CALLS) and its pipeline steps (STEPS).

Steps: `plan` (derive in code, then SPEC-PLAN, fused with the product for a compact app when the fusion rule says so) and
`product` (PROD-MODULE per module, PROD-INTEG, PROD-JOURNEYS, in parallel batches). The model writes JSON; code writes the
graph, mints ids and decides tiers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mde_spec_agent.lanes.product.plan.spec import SPEC as PLAN
from mde_spec_agent.lanes.product.plan_fused.spec import SPEC as PLAN_FUSED
from mde_spec_agent.lanes.product.prod_integ.spec import SPEC as PROD_INTEG
from mde_spec_agent.lanes.product.prod_journeys.spec import SPEC as PROD_JOURNEYS
from mde_spec_agent.lanes.product.prod_module.spec import SPEC as PROD_MODULE
from mde_spec_agent.lanes.product.steps.plan import plan
from mde_spec_agent.lanes.product.steps.product import product

CALLS: list[Any] = [
    PLAN,
    PLAN_FUSED,
    PROD_MODULE,
    PROD_INTEG,
    PROD_JOURNEYS,
]  # SpecCallSpec objects
STEPS: dict[str, Callable[[Any], Awaitable[None]]] = {"plan": plan, "product": product}
