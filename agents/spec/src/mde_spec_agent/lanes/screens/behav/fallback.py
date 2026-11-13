"""Deterministic fallback of BEHAV: what code already wrote for the category, else the library's default text, else the
template of the data file. The call is flagged; the behaviours are marked assumed and degraded."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.behav.mock import render
from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, BehOut


def fallback(inp: BehavIn) -> BehavOut:
    out: list[BehOut] = []
    for cat in inp.categories:
        text = cat.existing_text or cat.library_text
        if not text:
            text, _ = render(cat, inp)
        out.append(BehOut(category=cat.name, text=text, basis=[], assumed=True))
    return BehavOut(behaviors=out)
