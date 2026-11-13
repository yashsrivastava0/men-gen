"""Mock handler: deterministic, valid and plausible, derived from the input only (no clock, no randomness beyond `inp.seed`)."""

from __future__ import annotations

from mde_spec_agent.lanes.design.dsgn._compose import compose
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut


def handle(inp: DsgnIn) -> DsgnOut:
    return compose(inp)
