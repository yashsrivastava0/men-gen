"""Mock handler of ``cmp.write_card``: the deterministic card."""

from __future__ import annotations

from mde_compass.calls.write_card.fallback import fallback
from mde_compass.calls.write_card.schema import WriteCardInput, WriteCardOutput


def handle(inp: WriteCardInput) -> WriteCardOutput:
    return fallback(inp)
