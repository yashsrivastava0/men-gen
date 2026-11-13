"""Mock handler of the fused call: every part's own mock on its part, joined."""

from __future__ import annotations

from mde_spec_agent.lanes.product.plan.mock import handle as plan_mock
from mde_spec_agent.lanes.product.prod_integ.mock import handle as integ_mock
from mde_spec_agent.lanes.product.prod_journeys.mock import handle as journeys_mock
from mde_spec_agent.lanes.product.prod_module.mock import handle as module_mock

from .schema import FusedIn, FusedOut, join_parts


def handle(inp: FusedIn) -> FusedOut:
    return join_parts(
        plan_mock(inp.plan),
        module_mock(inp.module),
        integ_mock(inp.integ),
        journeys_mock(inp.journeys),
    )
