"""Offline mock of C6.plan_research: the deterministic plan derived from the Brief."""

from __future__ import annotations

from mde_contracts.calls import C6Input, C6Output
from mde_prompts.calls.c6_plan_research.plan import deterministic_plan


def handle(input: C6Input) -> C6Output:
    return deterministic_plan(input)
