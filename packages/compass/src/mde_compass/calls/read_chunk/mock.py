"""Offline handler for ``cmp.read_chunk``: the deterministic rule reader (schema-valid, never invents)."""

from __future__ import annotations

from mde_compass.calls.read_chunk.fallback import fallback
from mde_compass.calls.read_chunk.schema import ReadChunkInput, ReadChunkOutput


def handle(input: ReadChunkInput) -> ReadChunkOutput:
    return fallback(input)
