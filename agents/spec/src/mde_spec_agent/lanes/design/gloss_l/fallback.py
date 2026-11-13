"""Deterministic fallback: the Brief's own terms in the source language and NO extra-language entries.

The step then marks the extra locales degraded (the locale calls still run, flagged)."""

from __future__ import annotations

from mde_spec_agent.lanes.design.gloss_l.schema import GlossEntry, GlossIn, GlossOut


def fallback(inp: GlossIn) -> GlossOut:
    return GlossOut(
        entries=[GlossEntry(id=i.id, language=inp.source_language, text=i.text) for i in inp.items]
    )
