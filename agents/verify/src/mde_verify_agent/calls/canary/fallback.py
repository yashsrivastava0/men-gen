"""Deterministic fallback: the same code reading without the outline. The runner flags the call, so the card is never
shown as model-checked when it was not."""

from __future__ import annotations

from mde_verify_agent.calls.canary.analyse import analyse
from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut


def fallback(inp: CanaryIn) -> CanaryOut:
    return analyse(inp, with_outline=False)
