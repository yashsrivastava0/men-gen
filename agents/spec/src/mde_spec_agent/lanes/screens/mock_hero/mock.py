"""Mock handler of MOCK-HERO: deterministic and derived from the input only (record ids, writable fields, locale pools)."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.mock_hero.generate import generate
from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn, HeroOut


def handle(inp: HeroIn) -> HeroOut:
    return generate(inp)
