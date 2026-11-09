"""Phrase tables and small text helpers for the rule-based understander (data in ``lexicon.yaml``).

Everything here is deterministic, offline and linear in the length of the text: inputs are clipped to
``MAX_SCAN_CHARS`` before any matching, whitespace gaps in patterns are bounded, and phrase regexes are
literal alternations (no nested quantifiers). Adapted from the Discover lexicon helpers (copied, not imported).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

MAX_SCAN_CHARS = 20_000
"""The most text a rule-based handler looks at (the rest is ignored, never matched)."""

_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_APOSTROPHES = re.compile(r"['’‘`]")
_LEXICON_PATH = Path(__file__).with_name("lexicon.yaml")


def clip(text: str, limit: int = MAX_SCAN_CHARS) -> str:
    """At most ``limit`` characters."""
    return text if len(text) <= limit else text[:limit]


def squash_spaces(text: str) -> str:
    """Every run of whitespace becomes one space."""
    return _SPACES.sub(" ", text)


def normalize(text: str) -> str:
    """NFC, casefold, drop apostrophes, punctuation to spaces, collapse spaces."""
    out = unicodedata.normalize("NFC", clip(text)).casefold()
    out = _APOSTROPHES.sub("", out)
    out = _PUNCT.sub(" ", out)
    return _SPACES.sub(" ", out).strip()


def norm_for_quote(text: str) -> str:
    """Case-insensitive, whitespace-normalised form used to verify that a quote occurs in the user's text."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """True when ``quote`` is a non-empty case-insensitive, whitespace-normalised substring of ``text``."""
    q = norm_for_quote(quote)
    return bool(q) and q in norm_for_quote(text)


@lru_cache(maxsize=1024)
def _phrase_regex(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """One literal-alternation regex over the ORIGINAL text (case-insensitive, bounded whitespace gaps)."""
    parts = sorted({p.strip() for p in phrases if p.strip()}, key=len, reverse=True)
    if not parts:
        return re.compile(r"(?!x)x")
    alts = "|".join(r"\s{1,4}".join(re.escape(w) for w in p.split()) for p in parts)
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


def find_spans(text: str, phrases: tuple[str, ...] | list[str]) -> list[tuple[int, int]]:
    """Non-overlapping (start, end) spans of whole-word phrase hits in the original text."""
    rx = _phrase_regex(tuple(phrases))
    return [m.span() for m in rx.finditer(clip(text))]


def find_first(text: str, phrases: tuple[str, ...] | list[str]) -> tuple[int, int] | None:
    m = _phrase_regex(tuple(phrases)).search(clip(text))
    return m.span() if m else None


def has_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    """A phrase occurs as whole words (apostrophes folded so "dont" matches "don't")."""
    folded = _APOSTROPHES.sub("", clip(text))
    return _phrase_regex(tuple(phrases)).search(folded) is not None


def trim_fillers(norm: str, lex: Lexicon) -> str:
    """Strip polite filler words from both ends (a bounded number of passes)."""
    toks = norm.split()
    lead = [normalize(f).split() for f in lex.fillers["leading"]]
    trail = [normalize(f).split() for f in lex.fillers["trailing"]]
    for _ in range(3):
        changed = False
        for f in sorted(lead, key=len, reverse=True):
            if f and toks[: len(f)] == f and len(toks) > len(f):
                toks = toks[len(f) :]
                changed = True
                break
        for f in sorted(trail, key=len, reverse=True):
            if f and toks[-len(f) :] == f and len(toks) > len(f):
                toks = toks[: -len(f)]
                changed = True
                break
        if not changed:
            break
    return " ".join(toks)


@dataclass(frozen=True)
class Lexicon:
    raw: dict[str, Any]
    fillers: dict[str, list[str]]
    intents: dict[str, tuple[str, ...]]
    exact: dict[str, frozenset[str]]  # intent -> normalised phrases for whole-message matches
    neg_before: frozenset[str]
    neg_after: frozenset[str]
    injection: tuple[re.Pattern[str], ...]


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    raw: dict[str, Any] = yaml.safe_load(_LEXICON_PATH.read_text(encoding="utf-8"))
    intents = {k: tuple(str(p) for p in v) for k, v in raw["intents"].items()}
    exact = {k: frozenset(normalize(p) for p in v) for k, v in intents.items()}
    neg = raw["negation"]
    return Lexicon(
        raw=raw,
        fillers={k: [str(x) for x in v] for k, v in raw["fillers"].items()},
        intents=intents,
        exact=exact,
        neg_before=frozenset(normalize(w) for w in neg["before"]),
        neg_after=frozenset(normalize(w) for w in neg["after"]),
        injection=tuple(re.compile(p, re.IGNORECASE) for p in raw["safety"]["injection"]),
    )


def is_exact(norm_text: str, intent: str) -> bool:
    """The whole message (after filler trimming) is one of the intent's phrases."""
    lex = load_lexicon()
    return trim_fillers(norm_text, lex) in lex.exact[intent] or norm_text in lex.exact[intent]


def negated_at(text: str, span: tuple[int, int]) -> bool:
    """A negation word sits just before ("no payments") or just after ("reviews nahi") the span."""
    lex = load_lexicon()
    s, e = span
    before = normalize(text[max(0, s - 30) : s]).split()[-2:]
    after = normalize(text[e : e + 20]).split()[:2]
    return bool(lex.neg_before & set(before)) or bool(lex.neg_after & set(after))


def detect_language(text: str, hint: str = "en") -> str:
    """``hi`` for Devanagari, ``hi-Latn`` for Hindi in Latin letters, else the hint or ``en``."""
    t = clip(text)
    deva = sum(1 for ch in t if "ऀ" <= ch <= "ॿ")
    letters = sum(1 for ch in t if ch.isalpha())
    if letters and deva * 3 >= letters:
        return "hi"
    markers = frozenset(HINGLISH_MARKERS)
    toks = normalize(t).split()
    hits = sum(1 for tok in toks if tok in markers)
    if hits >= 2 or (toks and hits >= 1 and len(toks) <= 3):
        return "hi-Latn"
    return hint or "en"


HINGLISH_MARKERS: tuple[str, ...] = (
    "hai", "hain", "hoon", "nahi", "nahin", "kya", "kyun", "kaise", "kitna", "kitne", "mujhe", "mera",
    "meri", "mere", "aap", "apna", "apni", "tum", "hum", "aur", "bhi", "toh", "mein", "wala", "wali",
    "chahiye", "banana", "banani", "banao", "karo", "karna", "karein", "dukaan", "dukan", "liye", "ka",
    "ki", "ke", "ko", "pe", "haan", "theek", "thik", "bilkul", "pata", "bhai", "yaar", "sab", "kuch",
    "abhi", "phir", "baad", "shuru", "bas", "bana", "do", "kar", "jaisa", "jaise", "se", "me",
)  # fmt: skip
