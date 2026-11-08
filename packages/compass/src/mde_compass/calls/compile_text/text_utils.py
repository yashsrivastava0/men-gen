"""Small linear-time text helpers shared by the validator and the fallback of ``cmp.compile_text``.

Every regex here has no nested quantifiers: its repeated groups start with a character the previous part
cannot match, so matching is linear (tested with a 50k-character hostile input).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+(?:['’-][^\W\d_]+)*")
SENTENCE_END = frozenset(".!?।:;\n")
MAX_TOKEN = 40
MIN_STEM = 4
TEMPLATES_PATH = Path(__file__).parent / "templates.yaml"


@functools.lru_cache(maxsize=1)
def load_templates() -> dict[str, dict[str, Any]]:
    """The fallback wording per language (``en`` and ``hi``)."""
    raw = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in raw.items()}


def template_lang(language: str) -> str:
    """``hi`` for Devanagari Hindi; everything else (English, Hinglish in Latin script) uses ``en``."""
    low = language.strip().lower().replace("_", "-")
    if low in ("hi", "hi-in", "hindi"):
        return "hi"
    return "en"


def normalize_number(token: str) -> str:
    """Digits only (Devanagari digits folded to ASCII), thousands commas removed."""
    out = []
    for ch in token:
        if ch.isdigit():
            out.append(str(unicodedata.digit(ch, 0)))
        elif ch == ".":
            out.append(ch)
    return "".join(out)


def tokens(text: str) -> list[str]:
    return [t[:MAX_TOKEN] for t in TOKEN_RE.findall(text)]


def is_number(token: str) -> bool:
    return token[:1].isdigit()


def base_form(token: str) -> str:
    """Casefolded token without a possessive ending."""
    low = token.casefold()
    for suffix in ("'s", "’s"):
        if low.endswith(suffix) and len(low) > len(suffix):
            return low[: -len(suffix)]
    return low


def sentence_initial_flags(text: str) -> list[tuple[str, bool]]:
    """Tokens with a flag saying whether each one starts a sentence (linear scan)."""
    out: list[tuple[str, bool]] = []
    start = True
    pos = 0
    for match in TOKEN_RE.finditer(text):
        gap = text[pos : match.start()]
        if any(c in SENTENCE_END for c in gap):
            start = True
        out.append((match.group()[:MAX_TOKEN], start))
        start = False
        pos = match.end()
    return out


def clip(text: str, limit: int) -> str:
    """Cut at a word boundary within ``limit`` characters; never longer than ``limit``."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-")


class Vocabulary:
    """Everything a text says: numbers, words and word prefixes (for plural and inflection matches)."""

    def __init__(self, texts: Iterable[str], numbers: Iterable[int | str] = ()) -> None:
        self.words: set[str] = set()
        self.numbers: set[str] = {str(n) for n in numbers}
        for text in texts:
            for tok in tokens(text):
                if is_number(tok):
                    self.numbers.add(normalize_number(tok))
                else:
                    self.words.add(base_form(tok))
        self.prefixes: set[str] = {
            w[:k] for w in self.words for k in range(MIN_STEM, min(len(w), MAX_TOKEN) + 1)
        }

    def knows(self, word: str) -> bool:
        """``word`` is an input word, a prefix of one, or extends one (plural, inflection)."""
        if word in self.words or word in self.prefixes:
            return True
        return any(word[:k] in self.words for k in range(MIN_STEM, min(len(word), MAX_TOKEN)))

    def unknown_proper_nouns(self, text: str, allowed: frozenset[str] = frozenset()) -> list[str]:
        """Capitalized tokens of ``text`` that the input never used (``allowed`` words excepted)."""
        out: list[str] = []
        for tok in tokens(text):
            if is_number(tok) or not tok[:1].isupper():
                continue
            word = base_form(tok)
            if word not in allowed and not self.knows(word):
                out.append(tok)
        return out
