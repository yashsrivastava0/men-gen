"""Deterministic fallback: `tokens.pick_directions` chooses compliant directions, the words come from the lane's data file.

The lane never blocks on this call: the runner flags the result and the step marks the nodes degraded.
"""

from __future__ import annotations

from mde_spec_agent.lanes.design.dsgn._compose import compose
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut


def fallback(inp: DsgnIn) -> DsgnOut:
    return compose(inp)
