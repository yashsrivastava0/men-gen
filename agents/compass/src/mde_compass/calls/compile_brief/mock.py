"""Mock handler of ``cmp.compile_brief``: the deterministic floor (every fact placed, no items, no pitch).

It never pretends to understand the ledger: for offline tests and demos only, and in a mock session the Brief's
slot values come from the projector, so the compile adds the facts as details and nothing else.
"""

from __future__ import annotations

from mde_compass.calls.compile_brief.fallback import wire_fallback
from mde_compass.calls.compile_brief.schema import CompileInput, CompileOutput


def handle(inp: CompileInput) -> CompileOutput:
    return wire_fallback(inp)
