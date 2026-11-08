"""Offline handler for C1: the keyword understander (deterministic, schema-valid)."""

from __future__ import annotations

from mde_contracts.calls import C1Input, C1Output
from mde_prompts.calls.c1_understand.keyword import understand


def handle(input: C1Input) -> C1Output:
    return understand(input)
