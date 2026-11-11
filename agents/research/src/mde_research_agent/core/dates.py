"""Dates found on pages, parsed loosely and never guessed: ``2026-05-02``, ``2026-05``, ``2026`` only."""

from __future__ import annotations

import re
from datetime import date

_FULL = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_YEAR = re.compile(r"^(\d{4})$")


def parse_loose_date(text: str | None) -> date | None:
    """A ``date`` for ``YYYY-MM-DD``, ``YYYY-MM`` (first of the month) or ``YYYY`` (1 January), else None."""
    if not text:
        return None
    raw = text.strip()
    try:
        if m := _FULL.match(raw):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _MONTH.match(raw):
            return date(int(m[1]), int(m[2]), 1)
        if m := _YEAR.match(raw):
            return date(int(m[1]), 1, 1)
    except ValueError:
        return None
    return None
