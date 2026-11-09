"""Offline handler for ``cmp.merge_claims``: the deterministic merger (schema-valid, drops nothing)."""

from __future__ import annotations

from mde_compass.calls.merge_claims.fallback import fallback
from mde_compass.calls.merge_claims.schema import MergeInput, MergeOutput


def handle(input: MergeInput) -> MergeOutput:
    return fallback(input)
