"""Mock handler of ``cmp.answer``: the deterministic fallback, so offline runs are repeatable."""

from __future__ import annotations

from mde_compass.calls.answer.fallback import fallback
from mde_compass.calls.answer.schema import AnswerInput, AnswerOutput


def handle(inp: AnswerInput) -> AnswerOutput:
    return fallback(inp)
