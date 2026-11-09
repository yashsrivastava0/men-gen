"""Structural check on the decider's output. Everything finer is decided per item by the adapter (a bad decision for
one question must not void the other eleven), so the call-level validator only refuses an output that decides nothing."""

from __future__ import annotations

from mde_compass.calls.decide_answers.schema import DecideInput, DecideOutput


def structural(inp: DecideInput, out: DecideOutput) -> list[str]:
    if inp.items and not out.decisions:
        return ["no decisions for a request that has questions"]
    return []
