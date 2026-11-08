"""SQLAlchemy 2.0 declarative models: the schema of record (see docs/architecture/data-model.md).

Conventions
- Primary keys are prefixed ULIDs stored as ``text`` (``ses_``, ``job_``, ``apr_`` ...); composite
  natural keys where a row belongs to a session (``(session_id, version)``).
- Every timestamp is ``timestamptz`` and holds UTC. Structured payloads are ``jsonb``; the JSONB
  on a record table is the source of truth, the relational projections (``slot``,
  ``card_instance``, ``research_job``, ``research_fact``) are maintained by the repositories.
- Every tenant-scoped table has ``tenant_id``. Child tables reference ``session`` through the
  composite key ``(session_id, tenant_id)``, so the database itself refuses a row whose tenant
  differs from its session's tenant.
- Business rules live in the schema where they can: the partial unique index "one active run per
  session", ``(session_id, seq)`` uniqueness for SSE replay, ``(session_id, event_id)`` uniqueness
  for idempotent inbound events, and a trigger that makes ``spec_version`` content immutable.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
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

JOB_ACTIVE_STATES = ("queued", "running")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _session_fk(*, name: str | None = None) -> ForeignKeyConstraint:
    """Composite FK to ``session(id, tenant_id)``: tenant consistency enforced by the database."""
    return ForeignKeyConstraint(
        ["session_id", "tenant_id"],
        ["session.id", "session.tenant_id"],
        ondelete="CASCADE",
        name=name,
    )


# --------------------------------------------------------------------------- tenant / session


class TenantRow(Base):
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class SessionRow(Base):
    __tablename__ = "session"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        UniqueConstraint("id", "tenant_id", name="uq_session_id_tenant"),
        Index("ix_session_tenant_owner_updated", "tenant_id", "owner_id", "updated_at"),
        Index("ix_session_tenant_app", "tenant_id", "app_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    app_id: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    plan_phase: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    paused_from: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    discovery: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    brief_version: Mapped[int | None] = mapped_column(Integer)
    approved_brief_version: Mapped[int | None] = mapped_column(Integer)
    research_version: Mapped[int | None] = mapped_column(Integer)
    spec_version: Mapped[int | None] = mapped_column(Integer)
    handed_off_version: Mapped[int | None] = mapped_column(Integer)
    queued_messages: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    # Optimistic concurrency token: bumped by SessionRepo.save only.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Per-session outbound event counter (SSE replay). Locking this row serialises appenders;
    # it is NOT part of the CAS token, so appending events never conflicts with a save.
    outbound_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class SessionEventRow(Base):
    """Inbound and outbound events: the turn / event log, the SSE replay source and the
    idempotency ledger for inbound events (``(session_id, event_id)`` is the primary key)."""

    __tablename__ = "session_event"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "event_id"),
        _session_fk(),
        UniqueConstraint("session_id", "seq", name="uq_session_event_session_seq"),
        CheckConstraint("direction IN ('in', 'out')", name="direction"),
        CheckConstraint("(direction = 'out') = (seq IS NOT NULL)", name="seq_iff_outbound"),
        Index(
            "ix_session_event_reply",
            "session_id",
            "in_reply_to",
            postgresql_where=text("in_reply_to IS NOT NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int | None] = mapped_column(BigInteger)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- projections


class SlotRow(Base):
    """Projection of ``session.discovery.slots`` (rewritten on every session save)."""

    __tablename__ = "slot"
    __table_args__ = (PrimaryKeyConstraint("session_id", "slot_id"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    slot_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(TS)


class CardInstanceRow(Base):
    """Projection of ``card`` outbound events (shown) and their answers (``option_selected``)."""

    __tablename__ = "card_instance"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "instance_id"),
        _session_fk(),
        Index("ix_card_instance_session_card", "session_id", "card_id"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    card_id: Mapped[str] = mapped_column(Text, nullable=False)
    slot: Mapped[str] = mapped_column(Text, nullable=False)
    card_type: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)  # outbound seq of the card event
    shown_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    card: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # shown|answered|skipped|you_decide
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    answered_event_id: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(TS)


# --------------------------------------------------------------------------- brief / research / spec


class BriefVersionRow(Base):
    __tablename__ = "brief_version"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "version"),
        _session_fk(),
        CheckConstraint("status IN ('draft', 'approved', 'superseded')", name="status"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    brief: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    slots: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ResearchNoteRow(Base):
    __tablename__ = "research_note"
    __table_args__ = (PrimaryKeyConstraint("session_id", "version"), _session_fk())

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    brief_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ResearchJobRow(Base):
    """Projection: one row per research job (R1..R6) that produced facts in a note."""

    __tablename__ = "research_job"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "note_version", "job"),
        ForeignKeyConstraint(
            ["session_id", "note_version"],
            ["research_note.session_id", "research_note.version"],
            ondelete="CASCADE",
        ),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    note_version: Mapped[int] = mapped_column(Integer, nullable=False)
    job: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ResearchFactRow(Base):
    """Projection: one row per cited fact (URL, retrieval date, confidence)."""

    __tablename__ = "research_fact"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "note_version", "fact_id"),
        ForeignKeyConstraint(
            ["session_id", "note_version"],
            ["research_note.session_id", "research_note.version"],
            ondelete="CASCADE",
        ),
        Index("ix_research_fact_session_job", "session_id", "job"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    note_version: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    job: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    primary_url: Mapped[str | None] = mapped_column(Text)
    retrieved_on: Mapped[date | None] = mapped_column(Date)
    sources: Mapped[list[object]] = mapped_column(JSONB, nullable=False)


class SpecVersionRow(Base):
    """A spec version. ``spec_draft`` is a row here with status ``draft``.

    Content columns are immutable (database trigger ``spec_version_guard``); only ``status``,
    ``git_sha``, ``approved_at`` and ``updated_at`` change, along the allowed transitions.
    """

    __tablename__ = "spec_version"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "version"),
        _session_fk(),
        CheckConstraint(
            "status IN ('draft', 'approved', 'handed_off', 'superseded')", name="status"
        ),
        Index("ix_spec_version_session_status", "session_id", "status"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    spec_text: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    plan: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)
    research_version: Mapped[int | None] = mapped_column(Integer)
    git_sha: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ApprovalRow(Base):
    __tablename__ = "approval"
    __table_args__ = (
        PrimaryKeyConstraint("id"),
        _session_fk(),
        Index("ix_approval_session_gate_version", "session_id", "gate", "version"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    approver_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    via: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- standing data


class MemoryItemRow(Base):
    """Opt-in user or organisation memory (``scope`` is ``user`` or ``org``)."""

    __tablename__ = "memory_item"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "scope", "owner_id", "key"),
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        CheckConstraint("scope IN ('user', 'org')", name="scope"),
    )

    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)  # user id, or '' for org scope
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class OrgProfileRow(Base):
    __tablename__ = "org_profile"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
    )

    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    doc: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class PolicyRow(Base):
    """Per-tenant policy documents (``mde_contracts.policy.Policy`` as JSONB)."""

    __tablename__ = "policy"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
    )

    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[str] = mapped_column(Text, nullable=False)
    doc: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- LLM calls and jobs


class LlmCallRow(Base):
    """Mirror of the gateway's per-call record. No prompt text, ever."""

    __tablename__ = "llm_call"
    __table_args__ = (
        PrimaryKeyConstraint("call_id"),
        Index("ix_llm_call_session_at", "session_id", "at"),
        Index("ix_llm_call_scope", "scope"),
    )

    call_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    spec_id: Mapped[str] = mapped_column(Text, nullable=False)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)  # integer micro-dollars
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    repaired: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cassette_key: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


class JobRow(Base):
    __tablename__ = "job"
    __table_args__ = (
        PrimaryKeyConstraint("id"),
        _session_fk(),
        CheckConstraint("kind IN ('plan')", name="kind"),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="state"
        ),
        # One active (queued or running) run per session, enforced by the database.
        Index(
            "uq_job_one_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
        # Claim path: oldest queued job first.
        Index(
            "ix_job_queued_created",
            "created_at",
            "id",
            postgresql_where=text("state = 'queued'"),
        ),
        # Recovery path: running jobs by heartbeat age.
        Index(
            "ix_job_running_heartbeat",
            "heartbeat_at",
            postgresql_where=text("state = 'running'"),
        ),
        Index("ix_job_state_created", "state", "created_at"),
        Index("ix_job_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    lock_owner: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TS)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


ALL_MODELS: tuple[type[Base], ...] = (
    TenantRow,
    SessionRow,
    SessionEventRow,
    SlotRow,
    CardInstanceRow,
    BriefVersionRow,
    ResearchNoteRow,
    ResearchJobRow,
    ResearchFactRow,
    SpecVersionRow,
    ApprovalRow,
    MemoryItemRow,
    OrgProfileRow,
    PolicyRow,
    LlmCallRow,
    JobRow,
)
