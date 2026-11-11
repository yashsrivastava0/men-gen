"""Quote verification (RV-02): a quote is real only if CODE finds it in the stored page text.

Both the page and the quote are put in one normal form before the substring test:

* Unicode NFKC (ligatures, full-width letters, ``…`` -> ``...``, composed vs decomposed accents);
* zero-width and other format characters removed (ZWSP, ZWNJ, ZWJ, BOM, soft hyphen, bidi marks);
* every run of whitespace (NBSP, thin spaces, tabs, newlines) becomes one space;
* curly and angle quotes become ``'`` or ``"``, every dash and minus becomes ``-``;
* case-folded.

Combining marks are KEPT: in Devanagari, Kannada, Arabic and similar scripts they are letters.
Removing format characters from BOTH sides is symmetric, so it only makes the test tolerant of
invisible differences; it can never make a different sentence match.

A quote may be stitched with ``...`` between fragments; each fragment must be found, in order.
Too-short quotes are refused (a three-word fragment proves nothing). There is no fuzzy matching: the
model gets one retry ("quote exactly"), then the item is dropped.

``NormalizedText`` keeps, for every normalized character, the span it came from in the original text,
so a match can be located (and shown) in the page as it was stored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

_QUOTE_MAP = {
    **dict.fromkeys("‘’‚‛′‵`´ʼʹ", "'"),
    **dict.fromkeys("“”„‟″‶«»‹›", '"'),
}
_DASHES = "‐‑‒–—―−⸺⸻﹘﹣－"
_DASH_MAP = dict.fromkeys(_DASHES, "-")
_STITCH = re.compile(r"\.{3,}")
MIN_CHARS = 12
MIN_FRAGMENT_CHARS = 8
MIN_WORDS = 2

QuoteProblem = Literal["empty", "too_short", "not_found"]


@dataclass(frozen=True)
class NormalizedText:
    """``text`` in normal form plus, per character, the ``[start, end)`` span in the original."""

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    original_length: int

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """The span of the ORIGINAL text covered by normalized characters ``[start, end)``."""
        if not self.text or start >= end:
            return (0, 0)
        return (self.starts[start], self.ends[end - 1])


@dataclass(frozen=True)
class QuoteCheck:
    ok: bool
    problem: QuoteProblem | None = None
    start: int = 0  # span in the original page text (first fragment start .. last fragment end)
    end: int = 0
    fragments: int = 0


def _map_char(ch: str) -> str:
    """One normalized character -> its folded form ('' for removed ones, ' ' for whitespace)."""
    if ch in _QUOTE_MAP:
        return _QUOTE_MAP[ch]
    if ch in _DASH_MAP:
        return "-"
    cat = unicodedata.category(ch)
    if cat == "Cf" or ch in "­​‌‍⁠﻿":
        return ""
    if ch.isspace() or cat in {"Zs", "Zl", "Zp"} or ch in "\u0085":
        return " "
    if cat == "Cc":
        return " "
    # Case folding can leave a letter composed on one side and decomposed on the other (the Greek iota with
    # dialytika and tonos folds to three characters, its upper-case form to two), so the folded form is put in
    # one canonical shape: fully decomposed. Both the page and the quote go through here.
    return unicodedata.normalize("NFD", ch.casefold())


def _clusters(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each base character with its combining marks (NFKC is cluster-local)."""
    spans: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if i > 0 and unicodedata.category(ch).startswith("M"):
            continue
        if i > 0:
            spans.append((start, i))
        start = i
    if text:
        spans.append((start, len(text)))
    return spans


def normalize_with_offsets(text: str) -> NormalizedText:
    """Normalize ``text`` and remember where each normalized character came from."""
    if not text:
        return NormalizedText("", (), (), 0)
    if unicodedata.is_normalized("NFKC", text):
        units = [(ch, i, i + 1) for i, ch in enumerate(text)]
    else:
        units = []
        for s, e in _clusters(text):
            for ch in unicodedata.normalize("NFKC", text[s:e]):
                units.append((ch, s, e))
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    last_space = True  # drops leading whitespace
    for ch, s, e in units:
        mapped = _map_char(ch)
        for m in mapped:
            if m == " ":
                if last_space:
                    continue
                last_space = True
            else:
                last_space = False
            out.append(m)
            starts.append(s)
            ends.append(e)
    while out and out[-1] == " ":  # trailing whitespace
        out.pop()
        starts.pop()
        ends.pop()
    return NormalizedText("".join(out), tuple(starts), tuple(ends), len(text))


def normalize_text(text: str) -> str:
    """The normal form alone (idempotent: ``normalize_text(normalize_text(x)) == normalize_text(x)``)."""
    return normalize_with_offsets(text).text


def _fragments(norm_quote: str) -> list[str]:
    parts = [p.strip() for p in _STITCH.split(norm_quote)]
    return [p for p in parts if p]


def verify_quote(
    page: str | NormalizedText,
    quote: str,
    *,
    min_chars: int = MIN_CHARS,
    min_fragment_chars: int = MIN_FRAGMENT_CHARS,
    min_words: int = MIN_WORDS,
) -> QuoteCheck:
    """Is ``quote`` a verbatim (normalized) substring of ``page``? Fragments split on ``...`` in order."""
    norm_page = page if isinstance(page, NormalizedText) else normalize_with_offsets(page)
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return QuoteCheck(False, "empty")
    fragments = _fragments(norm_quote)
    if not fragments:
        return QuoteCheck(False, "empty")
    total = sum(len(f) for f in fragments)
    words = sum(len(f.split()) for f in fragments)
    if (
        total < min_chars
        or words < min_words
        or (len(fragments) > 1 and any(len(f) < min_fragment_chars for f in fragments))
    ):
        return QuoteCheck(False, "too_short")
    cursor = 0
    first_start = -1
    last_end = 0
    for frag in fragments:
        idx = norm_page.text.find(frag, cursor)
        if idx < 0:
            return QuoteCheck(False, "not_found")
        s, e = norm_page.original_span(idx, idx + len(frag))
        if first_start < 0:
            first_start = s
        last_end = e
        cursor = idx + len(frag)
    return QuoteCheck(True, None, first_start, last_end, len(fragments))


def word_count(text: str) -> int:
    return len(text.split())
