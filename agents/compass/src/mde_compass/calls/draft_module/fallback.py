"""The deterministic draft of ``cmp.draft_module`` (hard rule 4): screens from the patterns in
``catalog/structure_patterns.yaml`` for each feature of each module, journey segments over them, minimal entity fields and
access that is consistent by construction. See ``calls/structure_floor`` for what it is and is not."""

from __future__ import annotations

from mde_compass.calls.draft_module.schema import ModuleInput, ModuleOutput
from mde_compass.calls.structure_floor import floor_modules


def wire_fallback(inp: ModuleInput) -> ModuleOutput:
    return floor_modules(inp)
