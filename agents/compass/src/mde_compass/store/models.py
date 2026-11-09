"""SQLAlchemy 2.0 declarative models: the schema of record of the Compass store (prompt 5.8).

Conventions (adapted from ``mde_persistence.models``)
- Compass has its OWN ``MetaData`` and every table is prefixed ``compass_``, so it shares a database
  with the persistence package without touching its tables (own Alembic version table too).
- Every timestamp is ``timestamptz`` (UTC, from the injected clock). Structured payloads are
  ``jsonb``; the JSONB on a record table is the source of truth. Money is integer micro-dollars.
- Every tenant-scoped table has ``tenant_id`` and references ``compass_sessions`` through the
  composite key ``(session_id, tenant_id)``, so the database refuses a row whose tenant differs from
  its session's tenant. Deleting a session cascades.
- Business rules live in the schema where they can: ``(session_id, event_id)`` unique for inbound
  events, ``(session_id, seq)`` unique for outbound events (SSE replay), ``(session_id, version)``
  primary key of brief versions, one baseline label per session (partial unique index), a trigger that
  keeps a saved brief version immutable (only the baseline label may be set, once), and the
  ``active_turn`` / ``turn_started_at`` pair on the session row (claimed by a conditional UPDATE).
- ``compass_slots``, ``compass_features``, ``compass_assumptions``, ``compass_research_requests`` and
  ``compass_glossary`` are DERIVED projections rewritten by ``BriefRepo.save_version`` from the saved
  Brief and never read back.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TS = DateTime(timezone=True)
VERSION_TABLE = "compass_alembic_version"
SESSIONS = "compass_sessions"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _session_fk() -> ForeignKeyConstraint:
    """Composite FK to ``compass_sessions(id, tenant_id)``: tenant consistency enforced by the DB."""
    return ForeignKeyConstraint(
        ["session_id", "tenant_id"],
        [f"{SESSIONS}.id", f"{SESSIONS}.tenant_id"],
        ondelete="CASCADE",
    )


def _order_column() -> Mapped[int]:
    """Insertion-order counter (identity); upserts never change it, so listings keep their order."""
    return mapped_column(BigInteger, Identity(always=True), nullable=False)


# --------------------------------------------------------------------------- sessions


class SessionRow(Base):
    """One conversation. ``payload`` is the full ``SessionRecord``; the columns below it are the
    authority for everything the repositories change atomically (state, counters, turn flag)."""

    __tablename__ = SESSIONS
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_compass_sessions_id_tenant"),
        CheckConstraint("(NOT active_turn) OR turn_started_at IS NOT NULL", name="turn_started"),
        CheckConstraint("spent_micros >= 0 AND llm_calls >= 0", name="spend_non_negative"),
        Index("ix_compass_sessions_tenant_user_updated", "tenant_id", "user_id", "updated_at"),
        Index(
            "ix_compass_sessions_active_turn",
            "turn_started_at",
            postgresql_where=text("active_turn"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    # Optimistic concurrency token: bumped by SessionRepo.save and set_state only.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Per-session counters. Locking this row serialises appenders; they are NOT part of the CAS
    # token, so appending events or adding spend never conflicts with a save.
    outbound_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    event_idx: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    active_turn: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    turn_started_at: Mapped[datetime | None] = mapped_column(TS)
    spent_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- events (append-only)


class EventRow(Base):
    """Inbound and outbound events: the chat history, the SSE replay source and the idempotency ledger
    for inbound events. ``idx`` orders both directions; ``seq`` numbers outbound events only."""

    __tablename__ = "compass_events"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "idx"),
        _session_fk(),
        CheckConstraint("direction IN ('in', 'out')", name="direction"),
        CheckConstraint("(direction = 'out') = (seq IS NOT NULL)", name="seq_iff_outbound"),
        CheckConstraint("direction = 'out' OR event_id IS NOT NULL", name="inbound_has_event_id"),
        Index(
            "uq_compass_events_inbound_event_id",
            "session_id",
            "event_id",
            unique=True,
            postgresql_where=text("direction = 'in'"),
        ),
        Index(
            "uq_compass_events_session_seq",
            "session_id",
            "seq",
            unique=True,
            postgresql_where=text("seq IS NOT NULL"),
        ),
        Index(
            "ix_compass_events_turn",
            "session_id",
            "turn_id",
            postgresql_where=text("turn_id IS NOT NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    idx: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(Text)
    seq: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- sources, chunks, claims


class SourceRow(Base):
    __tablename__ = "compass_sources"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "id"),
        _session_fk(),
        UniqueConstraint("session_id", "sha256", name="uq_compass_sources_session_sha256"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    n: Mapped[int] = _order_column()


class ChunkRow(Base):
    __tablename__ = "compass_chunks"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "source_id", "chunk_id"),
        _session_fk(),
        ForeignKeyConstraint(
            ["session_id", "source_id"],
            ["compass_sources.session_id", "compass_sources.id"],
            ondelete="CASCADE",
        ),
        Index("ix_compass_chunks_order", "session_id", "source_id", "idx"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class ClaimRow(Base):
    __tablename__ = "compass_claims"
    __table_args__ = (PrimaryKeyConstraint("session_id", "claim_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    slot_hint: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    n: Mapped[int] = _order_column()


# --------------------------------------------------------------------------- question ledger


class QuestionRow(Base):
    __tablename__ = "compass_questions"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "card_id"),
        _session_fk(),
        CheckConstraint(
            "outcome IN ('open', 'answered', 'skipped', 'you_decide', 'back')", name="outcome"
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    card: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    why: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    asked_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(TS)
    n: Mapped[int] = _order_column()


# --------------------------------------------------------------------------- brief versions


class BriefVersionRow(Base):
    """A saved Brief version: append-only (a trigger refuses any change but setting the baseline label
    once)."""

    __tablename__ = "compass_brief_versions"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "version"),
        _session_fk(),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_compass_brief_versions_baseline",
            "session_id",
            "baseline_label",
            unique=True,
            postgresql_where=text("baseline_label IS NOT NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    brief: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    section_hashes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    baseline_label: Mapped[str | None] = mapped_column(Text)
    change_set_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- derived projections


class SlotRow(Base):
    """Projection: one row per filled slot of a dict section of the Brief (value, tier, history)."""

    __tablename__ = "compass_slots"
    __table_args__ = (PrimaryKeyConstraint("session_id", "slot_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    slot_id: Mapped[str] = mapped_column(Text, nullable=False)  # "<section>.<key>"
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    provenance: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    history: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)


class FeatureRow(Base):
    """Projection: the scope ledger (one row per feature of ``brief.scope``)."""

    __tablename__ = "compass_features"
    __table_args__ = (PrimaryKeyConstraint("session_id", "feature_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    feature_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scope_status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str | None] = mapped_column(Text)
    capability_tier: Mapped[str] = mapped_column(Text, nullable=False)
    custom: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)


class AssumptionRow(Base):
    __tablename__ = "compass_assumptions"
    __table_args__ = (PrimaryKeyConstraint("session_id", "assumption_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    assumption_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    slot_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ResearchRequestRow(Base):
    __tablename__ = "compass_research_requests"
    __table_args__ = (PrimaryKeyConstraint("session_id", "request_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    user_added: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)


class GlossaryRow(Base):
    __tablename__ = "compass_glossary"
    __table_args__ = (PrimaryKeyConstraint("session_id", "position"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)


# --------------------------------------------------------------------------- contributors, changes, calls


class ContributorRow(Base):
    __tablename__ = "compass_contributors"
    __table_args__ = (PrimaryKeyConstraint("session_id", "contributor_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    contributor_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    n: Mapped[int] = _order_column()


class ChangeSetRow(Base):
    __tablename__ = "compass_change_sets"
    __table_args__ = (PrimaryKeyConstraint("session_id", "change_set_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    change_set_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    n: Mapped[int] = _order_column()


class CallLogRow(Base):
    """One structured model call (append-only): tokens, cost in micro-dollars, latency, outcome."""

    __tablename__ = "compass_call_log"
    __table_args__ = (
        _session_fk(),
        Index("ix_compass_call_log_session", "session_id", "n"),
    )

    n: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(Text, nullable=False)
    spec_id: Mapped[str] = mapped_column(Text, nullable=False)
    spec_version: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Manage ``compass_*`` tables and their indexes only (never the version table itself)."""
    if type_ == "table":
        return bool(name) and str(name).startswith("compass_") and name != VERSION_TABLE
    if type_ == "index":
        table = getattr(obj, "table", None)
        table_name = getattr(table, "name", "") or ""
        return table_name.startswith("compass_") and table_name != VERSION_TABLE
    return True
