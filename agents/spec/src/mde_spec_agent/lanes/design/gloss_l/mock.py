"""Mock handler: deterministic. The source language keeps the Brief's words; every extra language gets the word tagged
with its language code (a stand-in, never a real translation)."""

from __future__ import annotations

from mde_spec_agent.lanes.design.gloss_l.schema import GlossEntry, GlossIn, GlossOut


def handle(inp: GlossIn) -> GlossOut:
    entries = [GlossEntry(id=i.id, language=inp.source_language, text=i.text) for i in inp.items]
    for lang in inp.languages:
        entries += [
            GlossEntry(id=i.id, language=lang, text=f"{i.text} [{lang}]") for i in inp.items
        ]
    return GlossOut(entries=entries)
