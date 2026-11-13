"""interview memory: compass_qa, compass_facts, compass_interview_summary, compass_batches

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 12:00:00+00:00

ADR 0015. Four tenant-bound tables (composite FK to compass_sessions, ON DELETE CASCADE) and the fact
guard: facts are never deleted (except together with their whole session) and a fact's id, creation
time, statement and quote never change.

Append-only: never edit this file once it has been applied anywhere; add a new revision instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _session_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["session_id", "tenant_id"],
        ["compass_sessions.id", "compass_sessions.tenant_id"],
        name=op.f(f"fk_{table}_session_id_compass_sessions"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "compass_qa",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=True),
        sa.Column("group_id", sa.Text(), nullable=True),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("card", _jsonb(), nullable=False),
        sa.Column(
            "answer", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("custom_text", sa.Text(), nullable=True),
        sa.Column("input_mode", sa.Text(), nullable=False),
        sa.Column("parent_card_id", sa.Text(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "input_mode IN ('tap', 'text', 'voice')", name=op.f("ck_compass_qa_input_mode")
        ),
        sa.CheckConstraint(
            "mode IS NULL OR mode IN ('initial', 'followup', 'replan', 'deepen', 'simplify')",
            name=op.f("ck_compass_qa_mode"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'skipped', 'you_decide', 'assumed', 'superseded', "
            "'dropped')",
            name=op.f("ck_compass_qa_status"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_compass_qa_version_positive")),
        _session_fk("compass_qa"),
        sa.PrimaryKeyConstraint("session_id", "card_id", "version", name=op.f("pk_compass_qa")),
    )
    op.create_index(
        "ix_compass_qa_session_batch", "compass_qa", ["session_id", "batch_id"], unique=False
    )
    op.create_index(
        "ix_compass_qa_session_status", "compass_qa", ["session_id", "status"], unique=False
    )

    op.create_table(
        "compass_facts",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("fact_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("topics", _jsonb(), nullable=False),
        sa.Column("entities", _jsonb(), nullable=False),
        sa.Column("consumers", _jsonb(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("qa_card_id", sa.Text(), nullable=True),
        sa.Column("claim_id", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("unverified_quote", sa.Boolean(), nullable=False),
        sa.Column("supersedes", _jsonb(), nullable=False),
        sa.Column("not_used_reason", sa.Text(), nullable=False),
        sa.Column("effects", _jsonb(), nullable=False),
        sa.Column("aspects", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_compass_facts_confidence_range")
        ),
        sa.CheckConstraint(
            "origin IN ('stated', 'tap', 'assumption', 'document')",
            name=op.f("ck_compass_facts_origin"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'disputed', 'not_used')",
            name=op.f("ck_compass_facts_status"),
        ),
        _session_fk("compass_facts"),
        sa.PrimaryKeyConstraint("session_id", "fact_id", name=op.f("pk_compass_facts")),
    )
    _create_fact_guard()

    op.create_table(
        "compass_interview_summary",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _session_fk("compass_interview_summary"),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_compass_interview_summary")),
    )

    op.create_table(
        "compass_batches",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("inputs_hash", sa.Text(), nullable=False),
        sa.Column("output", _jsonb(), nullable=False),
        sa.Column("accepted", _jsonb(), nullable=False),
        sa.Column("rejected", _jsonb(), nullable=False),
        sa.Column("verdicts", _jsonb(), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column("repaired", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _session_fk("compass_batches"),
        sa.PrimaryKeyConstraint("session_id", "batch_id", name=op.f("pk_compass_batches")),
    )
    op.create_index(
        "uq_compass_batches_session_mode_inputs",
        "compass_batches",
        ["session_id", "mode", "inputs_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_compass_batches_session_mode_inputs", table_name="compass_batches")
    op.drop_table("compass_batches")
    op.drop_table("compass_interview_summary")
    op.execute("DROP TRIGGER IF EXISTS trg_compass_fact_guard ON compass_facts")
    op.execute("DROP FUNCTION IF EXISTS compass_fact_guard()")
    op.drop_table("compass_facts")
    op.drop_index("ix_compass_qa_session_status", table_name="compass_qa")
    op.drop_index("ix_compass_qa_session_batch", table_name="compass_qa")
    op.drop_table("compass_qa")


def _create_fact_guard() -> None:
    """Facts are the interview's long-term memory: never deleted, and what was said never changes.

    * UPDATE may change a fact's status, links and metadata, never its id, session, tenant, creation
      time, statement or quote (a correction is a NEW fact that supersedes the old one).
    * DELETE is refused while the fact's session exists. The only way facts disappear is deleting the
      whole session (the ON DELETE CASCADE runs after the parent row is gone, so the session is no
      longer visible to this function then). TRUNCATE (test helper) does not fire row triggers.
    """
    op.execute(
        """
        CREATE FUNCTION compass_fact_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF EXISTS (SELECT 1 FROM compass_sessions WHERE id = OLD.session_id) THEN
                    RAISE EXCEPTION 'compass_facts: facts are never deleted';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.session_id IS DISTINCT FROM OLD.session_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.fact_id IS DISTINCT FROM OLD.fact_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.statement IS DISTINCT FROM OLD.statement
               OR NEW.quote IS DISTINCT FROM OLD.quote THEN
                RAISE EXCEPTION
                    'compass_facts: id, created_at, statement and quote of a fact are immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_compass_fact_guard BEFORE UPDATE OR DELETE ON compass_facts "
        "FOR EACH ROW EXECUTE FUNCTION compass_fact_guard()"
    )
