"""Offline stub of the lookup call: a SHAPE-NAME match. A need line that states `shape=<name>` is matched to the
components whose (answer-key) mapping includes that shape; a line without a shape (the card-only baseline) is matched by
the words it shares with each component's purpose. It tests the harness; it does not measure a model."""

from __future__ import annotations

import re

from evals.spec.calls.lookup.schema import LookupIn, LookupOut, MatchOut
from evals.spec.catalogues import by_library_name

_WORD = re.compile(r"[a-z]{4,}")
_SHAPE = re.compile(r"\bshape=([a-z_:]+)")
_STOP = {
    "that",
    "with",
    "from",
    "this",
    "what",
    "user",
    "shows",
    "show",
    "their",
    "have",
    "which",
    "when",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def handle(inp: LookupIn) -> LookupOut:
    cat = by_library_name(inp.library)
    out: list[MatchOut] = []
    for line in inp.items:
        item, _, desc = line.partition(":")
        item = item.strip()
        m = _SHAPE.search(desc)
        if m:
            names = cat.fitting(m.group(1)) or cat.maybe_fitting(m.group(1))
        else:
            dw = _words(desc)
            scored = sorted(
                ((len(dw & _words(c.purpose)), c.name) for c in cat.components),
                key=lambda t: (-t[0], t[1]),
            )
            names = [n for s, n in scored if s > 0]
        if names:
            out.append(MatchOut(item=item, candidates=names[:3], gap=False, gap_note=""))
        else:
            out.append(
                MatchOut(
                    item=item,
                    candidates=[],
                    gap=True,
                    gap_note="stub: nothing in the catalogue matches",
                )
            )
    return LookupOut(matches=out)


def fallback(inp: LookupIn) -> LookupOut:
    """The fallback declares nothing and names nothing (it never counts as a found gap or a hit); the runner flags it."""
    return LookupOut(
        matches=[
            MatchOut(item=line.partition(":")[0].strip(), candidates=[], gap=False, gap_note="")
            for line in inp.items
        ]
    )
