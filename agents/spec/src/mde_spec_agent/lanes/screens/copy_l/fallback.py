"""Deterministic fallback of COPY-L: the source text under each key (the app still runs; the locale is flagged degraded and
needs native review). Never invented words."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut, StrOut


def fallback(inp: CopyLIn) -> CopyLOut:
    return CopyLOut(strings=[StrOut(key=s.key, text=s.text) for s in inp.strings])
