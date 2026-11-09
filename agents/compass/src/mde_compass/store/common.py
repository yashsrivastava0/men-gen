"""Rules shared by both store implementations, so the fake and the real store cannot drift apart.

Nothing here touches a database. The values that logic depends on are named constants (the store has
no entry in ``mde_compass.config`` yet; see the store report).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import timedelta
from typing import TypeVar, cast

from pydantic import BaseModel

from mde_compass.domain.interview import Fact
from mde_core.errors import Conflict
from mde_persistence.jsonutil import strip_nul, utc

STALE_TURN_SECONDS = 120
"""A turn that started more than this long ago may be taken over (the worker that held it died)."""
STALE_TURN = timedelta(seconds=STALE_TURN_SECONDS)

DEFAULT_BASELINE_LABEL = "B-G1"
TURN_LOCK_KEY_PREFIX = "mde:compass:turn:"
TABLE_PREFIX = "compass_"
MAX_SEARCH_TEXT_CHARS = 200_000
"""Upper bound of the searchable text kept per event (the payload itself is never truncated)."""
FACT_ID_PREFIX = "FCT-"
BATCH_ID_PREFIX = "BAT-"
"""Prefixes of the ids the store hands out with ``next_id`` (``FCT-001``, ``BAT-001``; three digits or more)."""


def clean_json(value: object) -> object:
    """A JSON-compatible value without NUL characters (PostgreSQL rejects them in ``jsonb``)."""
    return strip_nul(value)


M = TypeVar("M", bound=BaseModel)


def normalize(model: M) -> M:
    """The model exactly as it comes back from storage: a JSON round trip with NUL characters removed.
    The in-memory store applies it on write so it behaves like PostgreSQL; the Brief is normalised
    before hashing so a stored hash always equals the hash of the stored content."""
    return type(model).model_validate(clean_json(model.model_dump(mode="json")))


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


def clean_text(value: str) -> str:
    """Text without NUL characters (PostgreSQL ``text`` rejects them); used for search input."""
    return value.replace("\x00", "")


def id_pattern(prefix: str) -> str:
    """Regex (same meaning in Python and PostgreSQL) of an id the store numbers: a literal prefix without
    metacharacters and ASCII digits only. Ids of any other shape are stored but never counted by
    ``next_id`` (so the PostgreSQL cast cannot fail on them)."""
    return f"^{prefix}[0-9]+$"


def id_number(prefix: str, value: str) -> int | None:
    """The number inside ``FCT-007`` (7), or ``None`` when ``value`` is not ``<prefix><ASCII digits>``."""
    if re.fullmatch(id_pattern(prefix), value, flags=re.ASCII) is None:
        return None
    return int(value[len(prefix) :])


def format_id(prefix: str, number: int) -> str:
    return f"{prefix}{number:03d}"


def next_numbered_id(prefix: str, existing: Iterable[str]) -> str:
    """The id after the highest existing one (the in-memory twin of the SQL ``max`` in PostgreSQL).
    Ids are never reused because nothing is ever deleted."""
    numbers = [n for n in (id_number(prefix, value) for value in existing) if n is not None]
    return format_id(prefix, max(numbers, default=0) + 1)


def in_utc(model: M, *fields: str) -> M:
    """The model with the named datetime fields as timezone-aware UTC (a naive value is taken as UTC), the
    form PostgreSQL gives back. The in-memory store applies it on write so both stores return equal values."""
    return model.model_copy(update={name: utc(getattr(model, name)) for name in fields})


def changed_fact_identity(stored: Fact, incoming: Fact) -> list[str]:
    """The immutable fields of a fact that ``incoming`` would change. The id is the lookup key, so it
    cannot differ; ``created_at`` ``None`` means "unchanged" (a fact built before it was ever stored)."""
    changed: list[str] = []
    if incoming.statement != stored.statement:
        changed.append("statement")
    if incoming.quote != stored.quote:
        changed.append("quote")
    if incoming.created_at is not None and utc(incoming.created_at) != stored.created_at:
        changed.append("created_at")
    return changed


def fact_immutable(session_id: str, fact_id: str, fields: list[str]) -> Conflict:
    return Conflict(
        f"fact {fact_id}: {', '.join(fields)} cannot change (a correction is a new fact that "
        "supersedes this one)",
        details={"session_id": session_id, "fact_id": fact_id, "fields": list(fields)},
    )
