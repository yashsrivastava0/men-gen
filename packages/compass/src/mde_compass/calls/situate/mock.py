"""Mock handler of ``cmp.situate``: the deterministic situation."""

from __future__ import annotations

from mde_compass.calls.situate.fallback import fallback
from mde_compass.calls.situate.schema import SituateInput, SituateOutput


def handle(inp: SituateInput) -> SituateOutput:
    return fallback(inp)
