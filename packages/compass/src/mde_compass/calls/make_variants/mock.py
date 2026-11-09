"""Mock handler of ``cmp.make_variants``: the deterministic catalog and family derivation."""

from __future__ import annotations

from mde_compass.calls.make_variants.fallback import fallback
from mde_compass.calls.make_variants.schema import VariantsIn, VariantsOut


def handle(inp: VariantsIn) -> VariantsOut:
    return fallback(inp)
