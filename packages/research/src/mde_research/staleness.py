"""When a Research Note is too old to trust (spec section 9: refresh on resume)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mde_contracts.research import ResearchNote

STALE_AFTER_DAYS = 14


def note_age_anchor(note: ResearchNote) -> datetime | None:
    """When the note was made: ``created_at``, else the oldest retrieval date of its facts."""
    if note.created_at is not None:
        created = note.created_at
        return created if created.tzinfo is not None else created.replace(tzinfo=UTC)
    dates = [source.retrieved_at for fact in note.facts for source in fact.sources]
    if dates:
        oldest = min(dates)
        return datetime(oldest.year, oldest.month, oldest.day, tzinfo=UTC)
    return None


def is_stale(note: ResearchNote, now: datetime, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """True when the note is older than ``max_age_days``. A note whose age cannot be established
    is treated as stale, because its freshness cannot be shown."""
    anchor = note_age_anchor(note)
    if anchor is None:
        return True
    return now - anchor > timedelta(days=max_age_days)
