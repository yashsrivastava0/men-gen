"""Passage selection: send the model the parts of a page that matter, not a blind truncation.

The page is cut into windows of whole sentences (full stops, the Devanagari danda and the Arabic question
mark count as sentence ends; blank lines too). Each window is scored with BM25 against the slot terms and
the best windows are kept, in page order, until the character budget is used. Every passage is an exact
slice of the stored text, so a quote copied from a passage is found by the verifier in the page.
Deterministic: ties break by position.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import regex

from mde_research_agent.core.text import stem, tokens

_SENT_END = regex.compile(r"(?<=[.!?।॥؟。])\s+|\n{2,}")
K1 = 1.5
B = 0.75
SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class Passage:
    start: int
    end: int
    text: str
    score: float


def _units(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _SENT_END.finditer(text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def windows(text: str, *, window_chars: int = 600) -> list[tuple[int, int]]:
    """Windows of whole sentences, each about ``window_chars`` long (a longer sentence stands alone)."""
    out: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    for s, e in _units(text):
        if cur_start is None:
            cur_start, cur_end = s, e
        elif e - cur_start <= window_chars:
            cur_end = e
        else:
            out.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    if cur_start is not None:
        out.append((cur_start, cur_end))
    return out


def _terms(raw: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for phrase in raw:
        for tok in tokens(phrase):
            seen.setdefault(stem(tok), None)
    return list(seen)


def bm25_scores(docs: Sequence[Sequence[str]], query: Sequence[str]) -> list[float]:
    """BM25 of each tokenised document against the query terms (documents are the windows)."""
    n = len(docs)
    if n == 0 or not query:
        return [0.0] * n
    avg = sum(len(d) for d in docs) / n or 1.0
    df: dict[str, int] = {q: sum(1 for d in docs if q in d) for q in query}
    scores: list[float] = []
    for d in docs:
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in query:
            f = tf.get(q, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * len(d) / avg))
        scores.append(round(s, 9))
    return scores


def select_passages(
    text: str,
    terms: Sequence[str],
    *,
    max_chars: int = 6000,
    window_chars: int = 600,
) -> list[Passage]:
    """The best windows of ``text`` for ``terms`` within ``max_chars``, in page order.

    A page that already fits is returned whole. Windows with a zero score are used only when nothing
    scored, so the model still gets the start of the page rather than nothing.
    """
    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [Passage(0, len(text), text, 0.0)]
    spans = windows(text, window_chars=window_chars)
    query = _terms(terms)
    docs = [[stem(t) for t in tokens(text[s:e])] for s, e in spans]
    scores = bm25_scores(docs, query)
    order = sorted(range(len(spans)), key=lambda i: (-scores[i], spans[i][0]))
    chosen: list[int] = []
    used = 0
    for i in order:
        if scores[i] <= 0 and chosen:
            break
        size = spans[i][1] - spans[i][0]
        if used + size > max_chars and chosen:
            continue
        chosen.append(i)
        used += size
        if used >= max_chars:
            break
    return [
        Passage(spans[i][0], spans[i][1], text[spans[i][0] : spans[i][1]], scores[i])
        for i in sorted(chosen)
    ]


def render(passages: Sequence[Passage]) -> str:
    """The text handed to the model: passages in page order, separated by a rule."""
    return SEPARATOR.join(p.text for p in passages)
