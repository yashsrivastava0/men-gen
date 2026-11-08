"""Offline handler for C11: the facts joined into a short sentence."""

from __future__ import annotations

from mde_contracts.calls import C11Input, C11Output
from mde_prompts.calls.c11_write_summary.template import summarise


def handle(input: C11Input) -> C11Output:
    return summarise(input)
