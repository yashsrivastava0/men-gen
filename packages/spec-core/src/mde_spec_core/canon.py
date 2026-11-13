"""Canonical bytes: the same value always gives the same text and hash (doc 15 5.5, V-34)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Sorted keys, no spaces, UTF-8 characters kept, LF only, no locale involvement."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def normalise_text(text: str) -> str:
    """LF endings, no trailing spaces on a line, exactly one final newline."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def stable_sorted(items: list[str]) -> list[str]:
    """Locale-independent ordering (code point order)."""
    return sorted(items)
