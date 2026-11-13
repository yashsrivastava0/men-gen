"""Mock handler of COPY-L: deterministic. It does not translate: it marks every string with its language, like
'[hi] Pay', so tests can see which locale a text belongs to and that placeholders survive. Real translation is the call's
job; the validators below hold both to the same rules."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut, StrOut


def handle(inp: CopyLIn) -> CopyLOut:
    tag = f"[{inp.language}] "
    return CopyLOut(strings=[StrOut(key=s.key, text=tag + s.text) for s in inp.strings])
