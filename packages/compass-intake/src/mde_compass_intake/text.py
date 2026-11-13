"""Small text helpers for matching names (copied from Research, D-175; the mojibake repair stays there). Pure code.

Tokens are runs of letters, combining marks and digits (the ``regex`` module's Unicode classes), so a
Devanagari or Kannada word with its vowel signs stays ONE token (Python's ``\\w`` splits it at the signs).
The stemmer only touches plain ASCII English-like words; every other script is compared as written
(casefolded). It is a matching aid, not a linguistic tool: two names are "the same thing" when their
stem sets are equal, or one contains the other (at least two stems), or they overlap enough.
"""

from __future__ import annotations

import unicodedata

import regex

_WORD = regex.compile(r"[\p{L}\p{M}\p{N}]+")
STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "by",
        "with",
        "at",
        "from",
        "into",
        "my",
        "your",
        "our",
        "their",
        "its",
        "is",
        "are",
        "be",
        "this",
        "that",
        "app",
        "apps",
        "application",
        "mobile",
        "new",
        "best",
        "top",
    ]
)


def tokens(text: str) -> list[str]:
    """Casefolded NFKC tokens of ``text`` in order."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def stem(word: str) -> str:
    """A crude English stem (booking -> book, payments -> pay, classes -> class); others unchanged."""
    if len(word) <= 3 or not (word.isascii() and word.isalpha()):
        return word
    w = word
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith(("ies", "ied")) and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and len(w) > 4 and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        w = w[:-2]
    else:
        for suf in (
            "ments",
            "ment",
            "ations",
            "ation",
            "ions",
            "ion",
            "ings",
            "ing",
            "ers",
            "er",
            "ed",
        ):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        else:
            if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
                w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def stems(text: str, *, stop: frozenset[str] = STOP_WORDS) -> tuple[str, ...]:
    """Stemmed content tokens of ``text`` in order (stop words dropped)."""
    return tuple(stem(t) for t in tokens(text) if t not in stop)


def stem_set(text: str, *, stop: frozenset[str] = STOP_WORDS) -> frozenset[str]:
    return frozenset(stems(text, stop=stop))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_thing(a: str, b: str, *, threshold: float = 0.6) -> bool:
    """Do two short names mean one thing? (``Online payment`` ~ ``Pay for orders online``.)"""
    sa, sb = stem_set(a), stem_set(b)
    if not sa or not sb:
        return a.casefold().strip() == b.casefold().strip()
    if sa == sb:
        return True
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(small) >= 2 and small <= big:
        return True
    return jaccard(sa, sb) >= threshold


def dedupe_names(items: list[tuple[str, str]], *, threshold: float = 0.6) -> dict[str, str]:
    """``{id: representative_id}``: each name maps to the FIRST id (in list order) that means the same."""
    reps: list[tuple[str, str]] = []
    out: dict[str, str] = {}
    for item_id, name in items:
        for rep_id, rep_name in reps:
            if same_thing(name, rep_name, threshold=threshold):
                out[item_id] = rep_id
                break
        else:
            reps.append((item_id, name))
            out[item_id] = item_id
    return out


def word_count(text: str) -> int:
    return len(text.split())


def clean_spaces(text: str) -> str:
    return " ".join(text.split())
