"""Mock handler of ``cmp.update_summary``: the deterministic merge (not user-facing, so no ``[mock]`` prefix)."""

from __future__ import annotations

from mde_compass.calls.update_summary.fallback import fallback
from mde_compass.calls.update_summary.schema import SummaryInput, SummaryOutput


def handle(inp: SummaryInput) -> SummaryOutput:
    return fallback(inp)
