"""Normalized keys: the same feature, screen, flow or entity gets one key however sources word it.

The extraction model assigns a lowercase key to each item ("class booking"); CODE reconciles them: two keys
in the same area that mean one thing (equal stems, one contains the other with at least two stems, or enough
overlap) join one group, and the group takes its most common key (ties: the shorter, then alphabetical),
so the result does not depend on the order items arrive in. ``match_brief`` links a norm to a Brief name
with a precision guard (a shared generic word alone never matches).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from mde_research_agent.core.text import jaccard, same_thing, stem_set, tokens


def clean_key(text: str) -> str:
    """A readable lowercase key: casefolded tokens joined by single spaces."""
    return " ".join(tokens(text))


def reconcile(
    keys: Iterable[tuple[str, str]], *, threshold: float = 0.6
) -> dict[tuple[str, str], str]:
    """``{(area, key): canonical_key}`` for every distinct ``(area, key)`` given (repeats count as weight)."""
    counts = Counter((area, clean_key(key)) for area, key in keys if clean_key(key))
    nodes = sorted(counts)
    parent = {n: n for n in nodes}

    def find(n: tuple[str, str]) -> tuple[str, str]:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if a[0] == b[0] and find(a) != find(b) and same_thing(a[1], b[1], threshold=threshold):
                parent[find(b)] = find(a)
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    out: dict[tuple[str, str], str] = {}
    for members in groups.values():
        best = min(members, key=lambda m: (-counts[m], len(m[1]), m[1]))
        for m in members:
            out[m] = best[1]
    return out


def match_brief(
    key: str,
    brief_names: Mapping[str, str],
    *,
    threshold: float = 0.6,
    generic: frozenset[str] = frozenset(),
) -> str | None:
    """The Brief id whose name means the same as ``key`` (best overlap, then lowest id), else None."""
    mine = stem_set(key) - generic
    best: tuple[float, str] | None = None
    for ident in sorted(brief_names):
        theirs = stem_set(brief_names[ident]) - generic
        if not mine or not theirs or not (mine & theirs):
            continue
        if same_thing(key, brief_names[ident], threshold=threshold):
            score = jaccard(mine, theirs)
            if best is None or score > best[0]:
                best = (score, ident)
    return best[1] if best else None
