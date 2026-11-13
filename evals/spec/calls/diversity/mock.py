"""Offline stub of the diversity critic: a word-overlap reading of the records. It only tests the harness; the owner
samples the live critic's answers."""

from __future__ import annotations

import re

from evals.spec.calls.diversity.schema import DiversityIn, DiversityOut

_WORD = re.compile(r"[a-z_]{3,}")


def _bag(lines: list[str]) -> set[str]:
    return {w for ln in lines for w in _WORD.findall(ln.partition(":")[2].lower())}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def handle(inp: DiversityIn) -> DiversityOut:
    if inp.question == "between_app":
        a = _bag([r for r in inp.records if r.startswith("A")])
        b = _bag([r for r in inp.records if r.startswith("B")])
        score = 1 + round(4 * (1 - _jaccard(a, b)))
        note = f"stub: {len(a & b)} words shared between A and B"
    else:
        bags = [_bag([r]) for r in inp.records]
        pairs = [_jaccard(x, y) for i, x in enumerate(bags) for y in bags[i + 1 :]]
        score = 1 + round(4 * (sum(pairs) / len(pairs))) if pairs else 3
        note = f"stub: mean word overlap across {len(bags)} records"
    return DiversityOut(score=max(1, min(5, score)), notes=[note])


def fallback(inp: DiversityIn) -> DiversityOut:
    return DiversityOut(score=3, notes=["fallback: no model answer"])
