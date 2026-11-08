"""Offline handler for C2: keyword archetype match plus the deterministic extractions."""

from __future__ import annotations

from mde_contracts.calls import C2Input, C2Output
from mde_prompts.calls.c2_interpret_brief.keyword import interpret


def handle(input: C2Input) -> C2Output:
    return interpret(input)
