"""Country facts from ``data/countries.yaml`` (alpha-2 code -> name, label, top-level domain, main language).

A lookup helper only: the research agent never hard-codes a country. Unknown names or codes return
``None`` and the caller marks the locale as weak.

``name`` is the value Tavily's ``country`` parameter takes and may be ``null`` (a country the Brief can name but
Tavily's enum does not list); ``label`` is the display name in that case. ``parse_countries`` reads a stated
country text that may name several ("Mexico, Guatemala and Costa Rica") into one entry per country.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from mde_research_agent.core.text import tokens
from mde_research_agent.domain.input import Country

COUNTRIES_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.yaml"

# Words and marks that join country names in a list, in the languages the Briefs come in: "Mexico, Guatemala and Costa
# Rica", "Mexique et Senegal", "Brasil e Portugal", "Mexico y Peru", "India & Nepal", "India/Nepal".
_CONNECTIVES = frozenset({"and", "y", "et", "e", "und", "&", "+", "/", ",", ";"})
_SPLIT = re.compile(r"\s*(?:[,;/&+]|\b(?:and|y|et|e|und)\b)\s*", re.IGNORECASE)


@lru_cache(maxsize=1)
def countries() -> dict[str, dict[str, str]]:
    """``{alpha2: {name, label, tld, language}}``; a missing file means no countries are known.

    ``name`` here is always the display name (lowercase English): for a country Tavily lists it is Tavily's value, for
    one it does not (``name: null`` in the file) it is the ``label``. The Tavily adapter reads the file itself and
    sees the null; nothing here is sent to a provider."""
    try:
        raw = yaml.safe_load(COUNTRIES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        code = "no" if key is False else "on" if key is True else str(key).lower()
        if isinstance(value, dict):
            entry = {k: str(v) for k, v in value.items() if v is not None}
            label = entry.get("label") or entry.get("name")
            if label:
                entry["label"] = entry["name"] = label
            out[code] = entry
    return out


@lru_cache(maxsize=1)
def _by_name() -> dict[str, str]:
    table: dict[str, str] = {}
    for code, info in countries().items():
        for field in ("name", "label"):
            if info.get(field):
                table[" ".join(tokens(info[field]))] = code
    table.update({code: code for code in countries()})
    return table


def code_for_name(text: str | None) -> str | None:
    """The alpha-2 code for a country name or code ('India', 'in', 'United Arab Emirates'), else None."""
    if not text:
        return None
    return _by_name().get(" ".join(tokens(text)))


def country_name(code: str | None) -> str | None:
    """The display name of a country code (lowercase English), whether or not Tavily lists it."""
    info = countries().get((code or "").lower())
    return info.get("label") if info else None


@lru_cache(maxsize=1)
def _names_with_connective() -> tuple[tuple[str, str], ...]:
    """Known names that contain a joining word themselves ("trinidad and tobago"), longest first, so a list split
    never cuts one in two. Empty for the countries listed today; the data decides."""
    found = [
        (name, code)
        for name, code in _by_name().items()
        if code != name and any(w in _CONNECTIVES for w in name.split())
    ]
    return tuple(sorted(found, key=lambda t: -len(t[0])))


def _display(text: str) -> str:
    return " ".join(w.capitalize() for w in text.split())


def parse_countries(text: str | None) -> tuple[Country, ...]:
    """Every country named in ``text``, in order, each with its alpha-2 code when known.

    ``"India"`` is one country; ``"Mexico, Guatemala and Costa Rica"`` is three. A part that is not a country in
    ``countries.yaml`` stays as the person wrote it, with ``code=None`` (the planner then searches without a
    country filter for it). A text that is a whole known name is never split."""
    raw = (text or "").strip()
    if not raw:
        return ()
    whole = code_for_name(raw)
    if whole:
        return (Country(name=_display(country_name(whole) or raw), code=whole),)
    protected: dict[str, str] = {}
    masked = raw
    for i, (name, code) in enumerate(_names_with_connective()):
        pattern = re.compile(r"\s+".join(re.escape(w) for w in name.split()), re.IGNORECASE)
        if pattern.search(masked):
            key = f"\x00{i}\x00"
            protected[key] = code
            masked = pattern.sub(key, masked)
    parts = [p.strip(" .\t\n") for p in _SPLIT.split(masked)]
    pairs = [(p, protected.get(p) or code_for_name(p)) for p in parts if p]
    if len(pairs) > 1 and not any(code for _, code in pairs):
        return (Country(name=raw, code=None),)  # "East and West Africa": one statement, not a list
    out: list[Country] = []
    for piece, code in pairs:
        name = _display(country_name(code) or piece) if code else piece
        if not any(c.name.casefold() == name.casefold() for c in out):
            out.append(Country(name=name, code=code))
    return tuple(out)
