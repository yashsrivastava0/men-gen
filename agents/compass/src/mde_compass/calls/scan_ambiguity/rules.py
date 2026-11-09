"""Loader for ``rules.yaml`` of the ambiguity scanner (cached, typed)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from mde_compass.calls.read_chunk.textutil import normalize

RULES_PATH = Path(__file__).with_name("rules.yaml")
LANGS = ("en", "hi", "hinglish")


@dataclass(frozen=True)
class ScanRules:
    max_findings: int
    max_items: int
    max_item_chars: int
    max_fact_lines: int
    max_percent: int
    max_finding_chars: int
    min_acronym_len: int
    max_acronym_len: int
    vague_words: frozenset[str]
    vague_phrases: tuple[str, ...]
    common_terms: frozenset[str]


@lru_cache(maxsize=1)
def load_rules() -> ScanRules:
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    lim = raw["limits"]
    words = [str(w) for lang in LANGS for w in raw["vague"]["words"].get(lang) or []]
    phrases = [str(p) for lang in LANGS for p in raw["vague"]["phrases"].get(lang) or []]
    return ScanRules(
        max_findings=int(lim["max_findings"]),
        max_items=int(lim["max_items"]),
        max_item_chars=int(lim["max_item_chars"]),
        max_fact_lines=int(lim["max_fact_lines"]),
        max_percent=int(raw["implausible"]["max_percent"]),
        max_finding_chars=int(lim["max_finding_chars"]),
        min_acronym_len=int(lim["min_acronym_len"]),
        max_acronym_len=int(lim["max_acronym_len"]),
        vague_words=frozenset(normalize(w) for w in words if normalize(w)),
        vague_phrases=tuple(normalize(p) for p in phrases if normalize(p)),
        common_terms=frozenset(str(t).casefold() for t in raw["common_terms"]),
    )
