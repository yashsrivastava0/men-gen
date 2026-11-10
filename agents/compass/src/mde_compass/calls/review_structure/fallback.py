"""The deterministic review of ``cmp.review_structure`` (hard rule 4): no findings. The code checks of
``engine/structure_checks`` already cover everything that can be decided without a model, so a failed critic is a
critic that stayed silent, flagged and counted."""

from __future__ import annotations

from mde_compass.calls.review_structure.schema import ReviewInput, ReviewOutput


def wire_fallback(inp: ReviewInput) -> ReviewOutput:
    return ReviewOutput(findings=[])
