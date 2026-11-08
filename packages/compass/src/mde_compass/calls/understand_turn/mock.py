"""Mock handler of ``cmp.understand_turn``: the deterministic rule-based understanding."""

from __future__ import annotations

from mde_compass.calls.understand_turn.fallback import fallback
from mde_compass.calls.understand_turn.schema import UnderstandInput, UnderstandOutput


def handle(inp: UnderstandInput) -> UnderstandOutput:
    return fallback(inp)
