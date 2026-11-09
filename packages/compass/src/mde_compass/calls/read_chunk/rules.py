"""Loader for ``rules.yaml``: the typed rule tables of the deterministic reader (cached)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.read_chunk.textutil import normalize

RULES_PATH = Path(__file__).with_name("rules.yaml")
LANGS = ("en", "hi", "hinglish")


@dataclass(frozen=True)
class TypeRule:
    type: str
    words: frozenset[str]
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class Rules:
    max_statements: int
    max_text_chars: int
    max_quote_chars: int
    min_quote_chars: int
    min_words_business_context: int
    abbreviations: frozenset[str]
    injection_markers: tuple[str, ...]
    types: tuple[TypeRule, ...]
    unit_symbols: frozenset[str]
    units: frozenset[str]
    slot_hints: dict[str, str | None]
    confidence: dict[str, str]


def _flat(block: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for lang in LANGS:
        out.extend(str(x) for x in (block or {}).get(lang) or [])
    return out


@lru_cache(maxsize=1)
def load_rules() -> Rules:
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    lim = raw["limits"]
    types = tuple(
        TypeRule(
            type=t["type"],
            words=frozenset(normalize(w) for w in _flat(t.get("words")) if normalize(w)),
            phrases=tuple(normalize(p) for p in _flat(t.get("phrases")) if normalize(p)),
        )
        for t in raw["types"]
    )
    nu = raw["number_units"]
    return Rules(
        max_statements=int(lim["max_statements"]),
        max_text_chars=int(lim["max_text_chars"]),
        max_quote_chars=int(lim["max_quote_chars"]),
        min_quote_chars=int(lim["min_quote_chars"]),
        min_words_business_context=int(lim["min_words_business_context"]),
        abbreviations=frozenset(str(a).casefold() for a in raw["abbreviations"]),
        injection_markers=tuple(
            normalize(m) for m in _flat(raw["injection_markers"]) if normalize(m)
        ),
        types=types,
        unit_symbols=frozenset(nu["symbols"]),
        units=frozenset(normalize(u) for u in _flat(nu) if normalize(u)),
        slot_hints=dict(raw["slot_hints"]),
        confidence=dict(raw["confidence"]),
    )
