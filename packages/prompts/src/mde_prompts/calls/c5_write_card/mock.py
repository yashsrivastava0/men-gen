"""Offline handler for C5: a generic card built from the slot's options."""

from __future__ import annotations

from mde_contracts.calls import C5Input, C5Output
from mde_prompts.calls.c5_write_card.template import write_card


def handle(input: C5Input) -> C5Output:
    return write_card(input)
