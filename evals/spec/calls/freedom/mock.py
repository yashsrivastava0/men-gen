"""Offline stub of the freedom probe: it picks an arrangement and an order from a HASH of the style seed and the screen.

It is not a consumer. It tests the harness (the share of screens that differ, the report, the owner file); the real
share is only measured by the weakest configured route. Deterministic: the same input gives the same output.
"""

from __future__ import annotations

from evals.spec.calls.freedom.schema import FreedomIn, FreedomOut
from evals.spec.common import stable_int, stable_order


def handle(inp: FreedomIn) -> FreedomOut:
    toks = sorted(inp.arrangements)
    token = toks[stable_int("token", inp.style_seed, inp.screen_id) % len(toks)]
    order = stable_order(inp.region_keys, f"{inp.style_seed}|{inp.screen_id}|order")
    return FreedomOut(
        arrangement=token,
        region_order=order,
        reason="stub: chosen from the style seed and the screen, not read from the card",
    )


def fallback(inp: FreedomIn) -> FreedomOut:
    """The deterministic fallback is the card's own (alphabetical) order and the first token: it never differs between
    seeds, so a fallback is visible as 'identical layouts' and the runner flags it."""
    return FreedomOut(
        arrangement=sorted(inp.arrangements)[0],
        region_order=list(inp.region_keys),
        reason="fallback: no model answer",
    )
