"""Deterministic fallback of SPEC-PLAN: the completed draft. The plan has no safe default, so the step treats a fallback
result as invalid and raises `PlanInvalid`; this exists for the fused call's graceful path and for tests."""

from __future__ import annotations

from .baseline import complete
from .schema import PlanIn, PlanOut


def fallback(inp: PlanIn) -> PlanOut:
    return complete(inp)
