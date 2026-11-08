"""Mock handler of ``cmp.seed_research``: the deterministic fallback, so offline runs are schema-valid."""

from __future__ import annotations

from mde_compass.calls.seed_research.fallback import fallback
from mde_compass.calls.seed_research.schema import SeedResearchInput, SeedResearchOutput


def handle(inp: SeedResearchInput) -> SeedResearchOutput:
    return fallback(inp)
