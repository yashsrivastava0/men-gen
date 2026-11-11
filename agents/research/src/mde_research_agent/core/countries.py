"""Country facts from ``data/countries.yaml`` (alpha-2 code -> name, top-level domain, main language).

A lookup helper only: the research agent never hard-codes a country. Unknown names or codes return
``None`` and the caller marks the locale as weak.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from mde_research_agent.core.text import tokens

COUNTRIES_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.yaml"


@lru_cache(maxsize=1)
def countries() -> dict[str, dict[str, str]]:
    """``{alpha2: {name, tld, language}}``; a missing file means no countries are known."""
    try:
        raw = yaml.safe_load(COUNTRIES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        code = "no" if key is False else "on" if key is True else str(key).lower()
        if isinstance(value, dict):
            out[code] = {k: str(v) for k, v in value.items()}
    return out


@lru_cache(maxsize=1)
def _by_name() -> dict[str, str]:
    table = {" ".join(tokens(info.get("name", ""))): code for code, info in countries().items()}
    table.update({code: code for code in countries()})
    return table


def code_for_name(text: str | None) -> str | None:
    """The alpha-2 code for a country name or code ('India', 'in', 'United Arab Emirates'), else None."""
    if not text:
        return None
    return _by_name().get(" ".join(tokens(text)))


def country_name(code: str | None) -> str | None:
    info = countries().get((code or "").lower())
    return info["name"] if info else None
