"""Mock handler of ``cmp.probe_handoff``: the deterministic planners, so offline runs are schema-valid."""

from __future__ import annotations

from mde_compass.calls.probe_handoff.fallback import fallback
from mde_compass.calls.probe_handoff.schema import ProbeHandoffInput, ProbeHandoffOutput


def handle(inp: ProbeHandoffInput) -> ProbeHandoffOutput:
    return fallback(inp)
