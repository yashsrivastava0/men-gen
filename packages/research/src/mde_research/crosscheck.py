"""Cross-check (code, spec 4.2): facts are merged, sourced, verified and numbered deterministically.

Rules enforced here, never left to a model:

* every fact has at least one source with a url and a retrieval date;
* near-identical statements are merged and their sources unioned (corroboration);
* a fact flagged ``impact=high`` needs two sources from independent registrable domains, otherwise
  it stays ``verified=False`` (shown as "unverified") and its confidence drops to ``low``;
* ids are ``F-001``... in job order then first-seen order, so equal inputs give equal ids.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from mde_contracts.research import (
    Confidence,
    FactKind,
    Impact,
    ResearchFact,
    ResearchJobId,
    SourceRef,
)
from mde_research.domains import host_of, registrable_domain, url_key

JOB_ORDER: tuple[ResearchJobId, ...] = ("R1", "R2", "R3", "R4", "R5", "R6")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "these",
        "this",
        "to",
        "was",
        "were",
        "with",
        "which",
        "can",
        "may",
        "often",
        "most",
        "many",
        "some",
        "also",
        "than",
        "then",
        "they",
        "them",
        "such",
    ]
)
_WORD = re.compile(r"[a-z0-9#]+")
_CONFIDENCE_RANK: dict[Confidence, int] = {"low": 0, "medium": 1, "high": 2}
_FACT_ID = re.compile(r"^F-(\d{3,})$")


@dataclass
class RawFact:
    """A validated fact of one search result, before merging."""

    order: int
    job: ResearchJobId
    kind: FactKind
    statement: str
    source: SourceRef
    impact: Impact = "low"
    confidence: Confidence = "medium"


@dataclass
class MergedFact:
    order: int
    job: ResearchJobId
    kind: FactKind
    statement: str
    sources: list[SourceRef] = field(default_factory=lambda: [])
    impact: Impact = "low"
    confidence: Confidence = "medium"


def statement_tokens(statement: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(statement.casefold()) if w not in _STOPWORDS)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def independent_domains(sources: Iterable[SourceRef]) -> int:
    """Number of distinct registrable domains among the sources."""
    return len({registrable_domain(host_of(s.url) or s.url) for s in sources})


def is_verified(impact: Impact, sources: Sequence[SourceRef]) -> bool:
    """Low-impact facts need one source; high-impact facts need two independent domains."""
    if not sources:
        return False
    return impact == "low" or independent_domains(sources) >= 2


def _add_source(sources: list[SourceRef], new: SourceRef) -> None:
    key = url_key(new.url)
    if all(url_key(s.url) != key for s in sources):
        sources.append(new)


def merge_raw_facts(raw: Sequence[RawFact], threshold: float = 0.75) -> list[MergedFact]:
    """Merge near-identical statements of the same job (word overlap >= ``threshold``)."""
    merged: list[MergedFact] = []
    tokens: list[frozenset[str]] = []
    for fact in sorted(raw, key=lambda f: f.order):
        toks = statement_tokens(fact.statement)
        target: MergedFact | None = None
        for existing, existing_tokens in zip(merged, tokens, strict=True):
            if existing.job == fact.job and (
                existing.statement.casefold() == fact.statement.casefold()
                or similarity(toks, existing_tokens) >= threshold
            ):
                target = existing
                break
        if target is None:
            merged.append(
                MergedFact(
                    order=fact.order,
                    job=fact.job,
                    kind=fact.kind,
                    statement=fact.statement,
                    sources=[fact.source],
                    impact=fact.impact,
                    confidence=fact.confidence,
                )
            )
            tokens.append(toks)
            continue
        _add_source(target.sources, fact.source)
        if fact.impact == "high":
            target.impact = "high"  # the cautious reading wins
        if _CONFIDENCE_RANK[fact.confidence] > _CONFIDENCE_RANK[target.confidence]:
            target.confidence = fact.confidence
    return merged


def max_fact_number(facts: Iterable[ResearchFact]) -> int:
    best = 0
    for fact in facts:
        match = _FACT_ID.match(fact.id)
        if match:
            best = max(best, int(match.group(1)))
    return best


def build_facts(
    merged: Sequence[MergedFact], *, first_number: int = 1, max_facts: int = 60
) -> list[ResearchFact]:
    """Number and verify merged facts. Over the cap, unverified facts are dropped first."""
    candidates = [(m, is_verified(m.impact, m.sources)) for m in merged if m.sources]
    if len(candidates) > max_facts:
        keep = sorted(candidates, key=lambda c: (not c[1], c[0].order))[:max_facts]
        candidates = sorted(keep, key=lambda c: c[0].order)
    ordered = sorted(candidates, key=lambda c: (JOB_ORDER.index(c[0].job), c[0].order))
    facts: list[ResearchFact] = []
    for offset, (item, verified) in enumerate(ordered):
        number = first_number + offset
        facts.append(
            ResearchFact(
                id=f"F-{number:03d}",
                job=item.job,
                kind=item.kind,
                statement=item.statement,
                sources=sorted(item.sources, key=lambda s: s.url),
                impact=item.impact,
                confidence=item.confidence if verified else "low",
                verified=verified,
            )
        )
    return facts


def source_ref(url: str, title: str | None, retrieved_at: date) -> SourceRef:
    host = host_of(url) or ""
    return SourceRef(
        url=url, title=title, domain=host.removeprefix("www.") or None, retrieved_at=retrieved_at
    )
