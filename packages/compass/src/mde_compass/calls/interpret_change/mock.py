"""Mock handler of ``cmp.interpret_change``: the deterministic rule-based interpretation."""

from __future__ import annotations

from mde_compass.calls.interpret_change.fallback import fallback
from mde_compass.calls.interpret_change.schema import ChangeIn, ChangeOut


def handle(inp: ChangeIn) -> ChangeOut:
    return fallback(inp)
