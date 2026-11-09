"""Small text helpers for the rule-based halves of the answer, interpret_change and make_variants calls.

Copy-adapted from ``mde_prompts.calls.c1_understand.lexicon`` (not imported: layering). Everything is
pure, deterministic and offline. Regexes here run on user text and are linear in its length: no two
adjacent unbounded quantifiers over overlapping classes, bounded whitespace gaps, and the text is clipped
(``MAX_SCAN_CHARS``) and whitespace-folded before any phrase matching.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

MAX_SCAN_CHARS = 20_000  # the most text a rule-based handler looks at

_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_APOSTROPHES = re.compile(r"['’‘`]")


def squash_spaces(text: str) -> str:
    """Every run of whitespace becomes one space."""
    return _SPACES.sub(" ", text)


def clip(text: str, limit: int = MAX_SCAN_CHARS) -> str:
    """At most ``limit`` characters (the rest is not looked at by rule-based handlers)."""
    return text if len(text) <= limit else text[:limit]


def normalize(text: str) -> str:
    """NFC, casefold, drop apostrophes, other punctuation to spaces, collapse spaces."""
    out = unicodedata.normalize("NFC", clip(text)).casefold()
    out = _APOSTROPHES.sub("", out)
    out = _PUNCT.sub(" ", out)
    return _SPACES.sub(" ", out).strip()


def words(text: str) -> list[str]:
    return normalize(text).split()


@lru_cache(maxsize=4096)
def _word_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def occurs(hay: str, phrase: str) -> bool:
    """The (already normalised) ``phrase`` occurs in the normalised ``hay`` as whole words."""
    return bool(phrase) and phrase in hay and _word_pattern(phrase).search(hay) is not None


def norm_all(items: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        n = normalize(str(item))
        if n:
            seen[n] = None
    return tuple(seen)


def has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Any normalised phrase occurs in the text as whole words."""
    hay = normalize(text)
    return any(occurs(hay, p) for p in phrases)


def hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    hay = normalize(text)
    return [p for p in phrases if occurs(hay, p)]


def pick(table: dict[str, str], lang: str | None) -> str:
    """The text for ``lang`` from a ``{en, hi, hi-Latn}`` table; English when missing."""
    if lang and lang in table:
        return table[lang]
    return table["en"]


def jaccard(a: str, b: str) -> float:
    """Token Jaccard similarity of two texts after normalisation (0.0 when both are empty)."""
    ta, tb = set(words(a)), set(words(b))
    if not ta and not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def load_rules(package: str) -> dict[str, Any]:
    """Read ``rules.yaml`` next to ``package`` (a dotted package name) and normalise phrase lists."""
    raw: dict[str, Any] = yaml.safe_load(
        resources.files(package).joinpath("rules.yaml").read_text(encoding="utf-8")
    )
    return {k: (norm_all(v) if isinstance(v, list) else v) for k, v in raw.items()}
