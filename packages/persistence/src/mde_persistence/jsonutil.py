"""Small conversion helpers between Pydantic records and database values."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel


def strip_nul(value: object) -> object:
    """Remove NUL characters from every string in a JSON-like value.

    PostgreSQL rejects ``\\u0000`` in ``jsonb`` and ``text``; user text can contain it, and a
    failing insert must never be the way a stray control character is discovered.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {
            (k.replace("\x00", "") if isinstance(k, str) else k): strip_nul(v)
            for k, v in cast("dict[object, object]", value).items()
        }
    if isinstance(value, list):
        return [strip_nul(v) for v in cast("list[object]", value)]
    return value


def clean_text(value: str | None) -> str | None:
    """NUL-free text (``None`` stays ``None``)."""
    return None if value is None else value.replace("\x00", "")


def dump(model: BaseModel) -> dict[str, object]:
    """The model as a JSON-compatible dict (datetimes as ISO strings), NUL-free."""
    return cast("dict[str, object]", strip_nul(model.model_dump(mode="json")))


def utc(value: datetime | None) -> datetime | None:
    """Normalise a database timestamp to timezone-aware UTC (naive values are taken as UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_required(value: datetime) -> datetime:
    out = utc(value)
    assert out is not None
    return out
