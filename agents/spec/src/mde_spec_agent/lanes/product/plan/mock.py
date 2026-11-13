"""Mock handler of SPEC-PLAN: it reproduces the baseline decisions (no merge, no split, the code's own release plan, the
draft's modules and owners) and writes a persona for every role from the role's goal (assumed). A merge appears only when
the baseline proposed one, and the baseline's merges were already applied by the derivation."""

from __future__ import annotations

from .baseline import complete
from .schema import PlanIn, PlanOut


def handle(inp: PlanIn) -> PlanOut:
    return complete(inp)
