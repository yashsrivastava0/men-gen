"""Deterministic fallback of SCRN: the baseline card code already wrote, unchanged, with no USUALLY and no COULD entries.
The runner flags the call and the step marks the screens degraded (a pack is never delivered with an unmarked hole)."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn, ScrnOut


def fallback(inp: ScrnIn) -> ScrnOut:
    return ScrnOut(
        cards=[s.card for s in inp.slots],
        regions=[r for s in inp.slots for r in s.regions],
        actions=[a for s in inp.slots for a in s.actions],
        inputs=[i for s in inp.slots for i in s.inputs],
        acceptance=[],
        usually=[],
        could=[],
        texts=[c for s in inp.slots for c in s.texts],
    )
