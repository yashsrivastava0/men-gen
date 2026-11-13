"""Deterministic fallback: the code-derived confirm moves and Spec's own differences; no model findings."""

from __future__ import annotations

from mde_verify_agent.calls.final_critic.analyse import confirm_moves, differs
from mde_verify_agent.calls.final_critic.schema import FinalIn, FinalOut


def fallback(inp: FinalIn) -> FinalOut:
    return FinalOut(
        findings=[],
        confirm_moves=confirm_moves(inp),
        differs_from_brief=differs(inp),
        summary="The model critic was not available; only the code checks were run.",
    )
