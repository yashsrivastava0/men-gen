"""Small deterministic text helpers shared by the derivation modules (no model, no locale-dependent calls)."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def slug(text: str, *, fallback: str = "item") -> str:
    """Lower snake case ascii (``Pay for order`` -> ``pay_for_order``); never empty, never starts with a digit."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    out = _NON_WORD.sub("_", folded).strip("_")
    if not out:
        return fallback
    return out if out[0].isalpha() else f"{fallback}_{out}"


def plural(noun: str) -> str:
    """A plain English plural for a lower-case noun phrase (the last word only)."""
    if not noun:
        return noun
    head, _, last = noun.rpartition(" ")
    if last.endswith(("s", "x", "ch", "sh")):
        out = last + "es" if not last.endswith("s") or last.endswith("ss") else last
    elif last.endswith("y") and last[-2:-1] not in "aeiou":
        out = last[:-1] + "ies"
    else:
        out = last + "s"
    return f"{head} {out}".strip()


def humanize(key: str) -> str:
    """``save_changes`` -> ``Save changes``."""
    words = key.replace("-", "_").split("_")
    text = " ".join(w for w in words if w)
    return text[:1].upper() + text[1:]


def first_sentences(text: str, n: int = 2) -> str:
    """The first ``n`` sentences of a free text, whitespace folded (a purpose is at most two sentences)."""
    folded = " ".join(text.split())
    if not folded:
        return ""
    parts = _SENTENCE.split(folded)
    return " ".join(parts[:n]).strip()


def normal(text: str) -> str:
    """Casefolded text with every run of non-letters/digits collapsed to one space (for whole-word matching)."""
    return " " + _NON_WORD.sub(" ", unicodedata.normalize("NFKD", text).lower()).strip() + " "


def has_word(text: str, words: list[str]) -> bool:
    """Whether any of ``words`` (a word or a short phrase) occurs in ``text`` as whole words."""
    hay = normal(text)
    return any(normal(w) in hay for w in words)
