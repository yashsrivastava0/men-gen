"""Deterministic fallback: no model findings. The code half of the critic still reports; the runner flags the call."""

from __future__ import annotations

from mde_verify_agent.calls.structure_critic.schema import StructureIn, StructureOut


def fallback(inp: StructureIn) -> StructureOut:
    return StructureOut(
        findings=[], summary="The model critic was not available; only the code checks were run."
    )
