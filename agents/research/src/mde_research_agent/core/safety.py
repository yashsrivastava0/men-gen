"""Forbidden-pattern check (RV-17 spirit, RS-06): findings must not carry URLs, instructions or secrets.

Pages are data. Extraction has no tools and its schema has no URL field, but a model can still copy an
instruction or a link into a claim. Every string of a finding is screened; a match drops the finding and is
counted. Patterns are data (``data/forbidden_patterns.yaml``).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

import yaml

from mde_research_agent.catalog.loader import DATA_DIR


@lru_cache(maxsize=1)
def _patterns() -> tuple[re.Pattern[str], ...]:
    raw = yaml.safe_load((DATA_DIR / "forbidden_patterns.yaml").read_text(encoding="utf-8")) or {}
    return tuple(re.compile(p, re.I) for p in raw.get("patterns", []))


def forbidden_in(text: str) -> str | None:
    """The first pattern that ``text`` matches (its source), else None."""
    norm = unicodedata.normalize("NFKC", text)
    for pat in _patterns():
        if pat.search(norm):
            return pat.pattern
    return None


def strings_of(value: Any) -> list[str]:
    """Every string inside nested lists and dicts."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_of(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in strings_of(v)]
    return []


def forbidden_in_value(value: Any) -> str | None:
    for s in strings_of(value):
        hit = forbidden_in(s)
        if hit:
            return hit
    return None
