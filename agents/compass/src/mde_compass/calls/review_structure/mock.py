"""Mock handler of ``cmp.review_structure``: no findings (the code checks are the offline critic). For offline tests and
demos only."""

from __future__ import annotations

from mde_compass.calls.review_structure.schema import ReviewInput, ReviewOutput


def handle(inp: ReviewInput) -> ReviewOutput:
    return ReviewOutput(findings=[])
