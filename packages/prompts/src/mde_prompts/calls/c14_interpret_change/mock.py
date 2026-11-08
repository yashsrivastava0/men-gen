"""Offline handler for C14: the rule-based change interpreter."""

from __future__ import annotations

from mde_contracts.calls import C14Input, C14Output
from mde_prompts.calls.c14_interpret_change.rules import interpret


def handle(input: C14Input) -> C14Output:
    return interpret(input)
