"""Structural check on the critic. A finding that does not cite a real id and a real fact is dropped by the engine one by
one, so a single bad finding never voids the rest; this validator only refuses a runaway list."""

from __future__ import annotations

from mde_compass.calls.review_structure.schema import ReviewInput, ReviewOutput

MAX_FINDINGS = 30


def structural(inp: ReviewInput, out: ReviewOutput) -> list[str]:
    if len(out.findings) > MAX_FINDINGS:
        return [f"{len(out.findings)} findings is not a review, it is noise"]
    return []
