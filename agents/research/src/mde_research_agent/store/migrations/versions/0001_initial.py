"""initial schema: rsch_runs, rsch_events, rsch_results, rsch_decisions and the append-only guard

Revision ID: 0001
Revises:
Create Date: 2026-11-11 12:00:00+00:00

Append-only: never edit this file once it has been applied anywhere; add a new revision instead.

* ``uq_rsch_runs_active_program`` is a PARTIAL UNIQUE index: one active (queued, running, committing) run per
  ``(tenant_id, program_id)``.
* ``rsch_append_only_guard`` keeps ``rsch_events``, ``rsch_results`` and ``rsch_decisions`` append-only: every
  UPDATE is refused, and a DELETE is refused while the run exists (the rows go away only with their run, through
  the ON DELETE CASCADE, which runs after the run row is gone). TRUNCATE does not fire row triggers (test helper).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARDED = ("rsch_events", "rsch_results", "rsch_decisions")


def upgrade() -> None:
    op.create_table(
        "rsch_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("brief_hash", sa.Text(), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.Column("load_class", sa.Text(), nullable=True),
        sa.Column("load_score", sa.Integer(), nullable=True),
        sa.Column("plan_source", sa.Text(), nullable=True),
        sa.Column("export_sha256", sa.Text(), nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_status", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("skip_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_rsch_runs"),
    )
    op.create_index(
        "ix_rsch_runs_tenant_program",
        "rsch_runs",
        ["tenant_id", "program_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_rsch_runs_active_program",
        "rsch_runs",
        ["tenant_id", "program_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'committing')"),
    )
    op.create_table(
        "rsch_events",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_rsch_events_seq_positive")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["rsch_runs.run_id"],
            name=op.f("fk_rsch_events_run_id_rsch_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_rsch_events"),
    )
    op.create_table(
        "rsch_results",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("research", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["rsch_runs.run_id"],
            name=op.f("fk_rsch_results_run_id_rsch_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_rsch_results"),
    )
    op.create_table(
        "rsch_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["rsch_runs.run_id"],
            name=op.f("fk_rsch_decisions_run_id_rsch_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rsch_decisions"),
    )
    op.create_index("ix_rsch_decisions_run", "rsch_decisions", ["run_id", "id"], unique=False)
    _create_append_only_guard()


def downgrade() -> None:
    for table in GUARDED:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_guard ON {table}")
    op.execute("DROP FUNCTION IF EXISTS rsch_append_only_guard()")
    op.drop_index("ix_rsch_decisions_run", table_name="rsch_decisions")
    op.drop_table("rsch_decisions")
    op.drop_table("rsch_results")
    op.drop_table("rsch_events")
    op.drop_index("uq_rsch_runs_active_program", table_name="rsch_runs")
    op.drop_index("ix_rsch_runs_tenant_program", table_name="rsch_runs")
    op.drop_table("rsch_runs")


def _create_append_only_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION rsch_append_only_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF EXISTS (SELECT 1 FROM rsch_runs WHERE run_id = OLD.run_id) THEN
                    RAISE EXCEPTION USING MESSAGE =
                        TG_TABLE_NAME || ': rows are append-only and are never deleted';
                END IF;
                RETURN OLD;
            END IF;
            RAISE EXCEPTION USING MESSAGE =
                TG_TABLE_NAME || ': rows are append-only and are never updated';
        END
        $$
        """
    )
    for table in GUARDED:
        op.execute(
            f"CREATE TRIGGER trg_{table}_guard BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION rsch_append_only_guard()"
        )
