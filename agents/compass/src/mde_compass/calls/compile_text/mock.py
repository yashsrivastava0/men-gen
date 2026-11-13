"""Mock handler of ``cmp.compile_text``: the deterministic fallback, so offline runs are schema-valid."""

from __future__ import annotations

from mde_compass.calls.compile_text.fallback import fallback
from mde_compass.calls.compile_text.schema import CompileTextInput, CompileTextOutput


def handle(inp: CompileTextInput) -> CompileTextOutput:
    return fallback(inp)
