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
    engines: int = 1  # how many search engines (providers) returned it (``fuse_engines``)


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


def fuse_engines(
    per_engine: Sequence[Sequence[Sequence[SearchHit]]], *, k: int = RRF_K
) -> list[FusedHit]:
    """Fuse the results of several search ENGINES so that agreement between engines counts.

    ``per_engine[i]`` holds the result lists engine ``i`` returned (one per query variant it was asked). Each engine's lists
    are fused first and its scores are averaged over its own lists, so an engine that was asked three variants does not
    outweigh one that was asked one; the engines' scores are then ADDED. A page both engines return therefore scores about
    twice a page only one of them returns at the same rank, whatever the number of variants each was asked. One engine gives
    the same order as ``fuse``. Pure and deterministic (scores rounded, ties by URL).
    """
    live = [lists for lists in per_engine if lists]
    if len(live) <= 1:
        return fuse(live[0], k=k) if live else []
    score: dict[str, float] = {}
    lists_in: dict[str, int] = {}
    engines_in: dict[str, int] = {}
    best: dict[str, SearchHit] = {}
    for lists in live:
        for fh in fuse(lists, k=k):
            score[fh.url] = score.get(fh.url, 0.0) + fh.score / len(lists)
            lists_in[fh.url] = lists_in.get(fh.url, 0) + fh.lists
            engines_in[fh.url] = engines_in.get(fh.url, 0) + 1
            cur = best.get(fh.url)
            if cur is None or _richer(fh.hit) < _richer(cur):
                best[fh.url] = fh.hit
    ordered = sorted(score, key=lambda u: (-round(score[u], 12), u))
    return [FusedHit(u, best[u], round(score[u], 12), lists_in[u], engines_in[u]) for u in ordered]
