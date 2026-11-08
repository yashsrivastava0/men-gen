"""Rules shared by both store implementations, so the fake and the real store cannot drift apart.

Nothing here touches a database. The values that logic depends on are named constants (the store has
no entry in ``mde_compass.config`` yet; see the store report).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import cast

from pydantic import BaseModel

from mde_persistence.jsonutil import strip_nul

STALE_TURN_SECONDS = 120
"""A turn that started more than this long ago may be taken over (the worker that held it died)."""
STALE_TURN = timedelta(seconds=STALE_TURN_SECONDS)

DEFAULT_BASELINE_LABEL = "B-G1"
TURN_LOCK_KEY_PREFIX = "mde:compass:turn:"
TABLE_PREFIX = "compass_"
MAX_SEARCH_TEXT_CHARS = 200_000
"""Upper bound of the searchable text kept per event (the payload itself is never truncated)."""


def clean_json(value: object) -> object:
    """A JSON-compatible value without NUL characters (PostgreSQL rejects them in ``jsonb``)."""
    return strip_nul(value)


def dump_model(model: BaseModel) -> dict[str, object]:
    """The model as a JSON-compatible dict (``mode="json"``), NUL-free."""
    return cast("dict[str, object]", clean_json(model.model_dump(mode="json")))


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in cast("dict[object, object]", value).values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            yield from _strings(item)


def payload_text(payload: object) -> str:
    """The searchable text of an event payload: every string value, joined, case-folded.

    Keys and non-string values are not searched, so a query like ``type`` does not match everything.
    Both stores filter ``list_history(q=...)`` by a plain substring of this text.
    """
    text = " ".join(_strings(payload)).casefold()
    return text[:MAX_SEARCH_TEXT_CHARS]


def turn_lock_key(session_id: str) -> str:
    return f"{TURN_LOCK_KEY_PREFIX}{session_id}"
