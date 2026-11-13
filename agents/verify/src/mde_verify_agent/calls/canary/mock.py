"""Mock handler: the code reading plus a plain outline, derived from the card's reads only."""

from __future__ import annotations

from mde_verify_agent.calls.canary.analyse import analyse
from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut


def handle(inp: CanaryIn) -> CanaryOut:
    return analyse(inp, with_outline=True)
