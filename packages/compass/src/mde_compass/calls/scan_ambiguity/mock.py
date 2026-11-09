"""Offline handler for ``cmp.scan_ambiguity``: the deterministic scanner (schema-valid, never changes values)."""

from __future__ import annotations

from mde_compass.calls.scan_ambiguity.fallback import fallback
from mde_compass.calls.scan_ambiguity.schema import ScanInput, ScanOutput


def handle(input: ScanInput) -> ScanOutput:
    return fallback(input)
