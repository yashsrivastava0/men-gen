"""SQLAlchemy 2.0 declarative models: the schema of record of the Research store.

Conventions (the same as the Compass store)
- Research has its OWN ``MetaData`` and every table is prefixed ``rsch_``, so it shares a database with the other
  packages without touching their tables (own Alembic version table ``research_alembic_version`` too).
- Every timestamp is ``timestamptz`` (UTC). Structured payloads are ``jsonb``; the JSONB is the source of truth.
- ``rsch_runs``: one row per run. A PARTIAL UNIQUE index on ``(tenant_id, program_id)`` for the active statuses
  (``queued``, ``running``, ``committing``) makes "one active run per program" a rule of the database, not only of
  the code.
- ``rsch_events``: the run's event log, primary key ``(run_id, seq)``; ``seq`` is contiguous (the store checks it
  under the run's row lock). Append-only (trigger).
- ``rsch_results``: the committed ``research.json`` and its evidence, one row per run (primary key ``run_id``).
  IMMUTABLE: a trigger refuses every UPDATE and every direct DELETE (the rows go away only with their run).
- ``rsch_decisions``: the owner's rejections, an overlay on the immutable result. Append-only (trigger).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Text,
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
TABLE_PREFIX = "rsch_"
VERSION_TABLE = "research_alembic_version"
RUNS = "rsch_runs"
ACTIVE_RUN_INDEX = "uq_rsch_runs_active_program"
RUNS_PK = "pk_rsch_runs"
GUARD_FUNCTION = "rsch_append_only_guard"
"""The trigger function that keeps events, results and decisions append-only (created by migration 0001)."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RunModel(Base):
    """One research run. The columns mirror ``store.ports.RunRow``."""

    __tablename__ = RUNS
    __table_args__ = (
        PrimaryKeyConstraint("run_id", name=RUNS_PK),
        Index("ix_rsch_runs_tenant_program", "tenant_id", "program_id", "created_at"),
        Index(
            ACTIVE_RUN_INDEX,
            "tenant_id",
            "program_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'committing')"),
        ),
    )

    run_id: Mapped[str] = mapped_column(Text)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    program_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    brief_hash: Mapped[str] = mapped_column(Text, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)
    load_class: Mapped[str | None] = mapped_column(Text)
    load_score: Mapped[int | None] = mapped_column(Integer)
    plan_source: Mapped[str | None] = mapped_column(Text)
    export_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_status: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    skip_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class EventModel(Base):
    """One event of a run; ``event`` is the full ``Event`` (``model_dump(mode="json", by_alias=True)``)."""

    __tablename__ = "rsch_events"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "seq", name="pk_rsch_events"),
        CheckConstraint("seq >= 1", name="seq_positive"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey(f"{RUNS}.run_id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ResultModel(Base):
    """The committed ``research.json`` (``research``), its evidence and its content hash: immutable."""

    __tablename__ = "rsch_results"
    __table_args__ = (PrimaryKeyConstraint("run_id", name="pk_rsch_results"),)

    run_id: Mapped[str] = mapped_column(ForeignKey(f"{RUNS}.run_id", ondelete="CASCADE"))
    research: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class DecisionModel(Base):
    """One owner decision about one item of a run's result (an overlay; the result never changes)."""

    __tablename__ = "rsch_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_rsch_decisions"),
        Index("ix_rsch_decisions_run", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    run_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RUNS}.run_id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Manage ``rsch_*`` tables and their indexes only (never the version table itself)."""
    if type_ == "table":
        return bool(name) and str(name).startswith(TABLE_PREFIX)
    if type_ == "index":
        table = getattr(obj, "table", None)
        table_name = getattr(table, "name", "") or ""
        return table_name.startswith(TABLE_PREFIX)
    return True
