"""Deterministic fallback of MOCK-HERO: seeded values from the locale pools (hero content is never left empty; the call is
flagged and the records are listed as degraded at G2)."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.mock_hero.generate import generate
from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn, HeroOut


def fallback(inp: HeroIn) -> HeroOut:
    return generate(inp)
