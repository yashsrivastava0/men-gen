"""Offline handler for C3: pick a knowledge snippet by keyword, otherwise say it is unknown."""

from __future__ import annotations

from mde_contracts.calls import C3Input, C3Output
from mde_prompts.calls.c3_respond.snippets import respond


def handle(input: C3Input) -> C3Output:
    return respond(input)
