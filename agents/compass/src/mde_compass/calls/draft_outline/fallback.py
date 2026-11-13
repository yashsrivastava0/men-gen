"""The deterministic outline of ``cmp.draft_outline`` (hard rule 4): built from the Brief's own features, roles and apps and
the templates in ``catalog/structure_patterns.yaml``; see ``calls/structure_floor`` for what it is and is not."""

from __future__ import annotations

from mde_compass.calls.draft_outline.schema import OutlineInput, OutlineOutput
from mde_compass.calls.structure_floor import floor_outline


def wire_fallback(inp: OutlineInput) -> OutlineOutput:
    return floor_outline(inp)
