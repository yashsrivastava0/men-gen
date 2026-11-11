"""Reciprocal rank fusion over several search result lists (k = 60) with canonical-URL dedupe.

A page that several query variants return rises; no model call is needed. Pure and deterministic: scores
are rounded, ties break by the first list position then by URL, so the same inputs give the same order
whatever the provider's timing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mde_research_agent.core.urls import canonical_url
from mde_research_agent.ports.search import SearchHit

RRF_K = 60


@dataclass(frozen=True)
class FusedHit:
    url: str  # canonical
    hit: SearchHit  # the richest copy (one with page content, else the best ranked)
    score: float
    lists: int  # how many result lists contained it


def _richer(hit: SearchHit) -> tuple[int, int, str, str]:
    """Sort key for 'the best copy of a page': one with text first, then the best provider rank."""
    return (0 if hit.content else 1, hit.rank or 10_000, hit.title, hit.url)


def fuse(results: Sequence[Sequence[SearchHit]], *, k: int = RRF_K) -> list[FusedHit]:
    """Merge ``results`` (one list per query variant) into one ranked, deduplicated list.

    The result does not depend on the order of the lists (scores add; ties break by URL).
    """
    scores: dict[str, float] = {}
    seen_in: dict[str, int] = {}
    best: dict[str, SearchHit] = {}
    for hits in results:
        listed: set[str] = set()
        for pos, hit in enumerate(hits, start=1):
            url = canonical_url(hit.url)
            if not url:
                continue
            scores[url] = scores.get(url, 0.0) + 1.0 / (k + (hit.rank or pos))
            if url not in listed:
                seen_in[url] = seen_in.get(url, 0) + 1
                listed.add(url)
            cur = best.get(url)
            if cur is None or _richer(hit) < _richer(cur):
                best[url] = hit
    ordered = sorted(scores, key=lambda u: (-round(scores[u], 12), u))
    return [FusedHit(u, best[u], round(scores[u], 12), seen_in[u]) for u in ordered]
