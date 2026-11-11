"""Query anatomy (RV-06 hygiene and RV-14 anatomy): every query must be searchable and specific.

A query has an ANCHOR (a named entity, a discovered entity, or a domain noun from the user's own words), a
PLACE (the stated geography, or the country plus every stated city when uncertain) and an INTENT word
(features, fees, rules, API, sandbox, limits, design, UX, ... per language, from data). It is short (about
4 to 12 words, at most 25) and carries no URL or key-like string. Category-only queries and queries that are
only the user's own brand name are refused, and nothing that fails is ever sent to a provider.

Matching is by stems for plain English words and by casefolded tokens for other scripts, so a Hindi,
Kannada or Arabic query passes with its own intent words.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from mde_research_agent.core.text import stem, tokens

MAX_WORDS = 25
_URLISH = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(com|org|net|in|io|gov|co|app|dev|ai)\b/?\S*)", re.I
)
_KEYISH = re.compile(
    r"(\b(sk|pk|tvly|key|token|api)[-_][A-Za-z0-9_\-]{10,}\b|\b[A-Za-z0-9_\-]{32,}\b|\b[0-9a-f]{24,}\b)"
)


@dataclass(frozen=True)
class QueryCheck:
    ok: bool
    problems: tuple[str, ...] = ()


def _stems(text: str) -> list[str]:
    return [stem(t) for t in tokens(text)]


def _phrase_in(query: list[str], phrase: str, generic: frozenset[str]) -> bool:
    """All content stems of ``phrase`` occur in the query (and the phrase has at least one)."""
    parts = [s for s in _stems(phrase) if s not in generic]
    return bool(parts) and all(p in query for p in parts)


def check_query(
    query: str,
    *,
    anchors: Iterable[str],
    places: Iterable[str],
    intent_words: Iterable[str],
    generic_words: Iterable[str] = (),
    own_brand: Iterable[str] = (),
    require_place: bool = True,
    max_words: int = MAX_WORDS,
) -> QueryCheck:
    """Why ``query`` may not be sent (empty ``problems`` means it may)."""
    problems: list[str] = []
    text = query.strip()
    if not text:
        return QueryCheck(False, ("empty",))
    if _URLISH.search(text):
        problems.append("url")
    if _KEYISH.search(text):
        problems.append("key_like")
    if len(text.split()) > max_words:
        problems.append("too_long")
    q = _stems(text)
    generic = frozenset(stem(w) for w in generic_words)
    brand_stems = [frozenset(_stems(b)) for b in own_brand if _stems(b)]
    # The user's own brand never counts as an anchor, and a query that is only the brand is refused.
    anchor_ok = any(
        _phrase_in(q, a, generic) and not any(frozenset(_stems(a)) <= b for b in brand_stems)
        for a in anchors
    )
    if not anchor_ok:
        problems.append("no_anchor")
    brand_in = any(b and b <= set(q) for b in brand_stems)
    rest = [s for s in q if s not in generic and not any(s in b for b in brand_stems)]
    if brand_in and not rest:
        problems.append("own_brand_alone")
    if require_place and not any(_phrase_in(q, p, generic) for p in places):
        problems.append("no_place")
    intent = {stem(w) for w in intent_words}
    if not any(s in intent for s in q):
        problems.append("no_intent")
    return QueryCheck(not problems, tuple(problems))


def normalize_query(query: str) -> str:
    """The form used to compare queries for repeats: casefolded tokens joined by one space."""
    return " ".join(tokens(query))


def is_repeat(query: str, earlier: Sequence[str]) -> bool:
    """Is ``query`` the same search as an earlier one (same tokens, any order)?"""
    key = sorted(tokens(query))
    return any(sorted(tokens(e)) == key for e in earlier)
