"""initial schema: every compass_* table, the event/brief constraints and the brief-version guard

Revision ID: 0001
Revises:
Create Date: 2026-11-09 03:33:00+00:00

Append-only: never edit this file once it has been applied anywhere; add a new revision instead.
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


def upgrade() -> None:
    op.create_table(
        "compass_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("outbound_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("event_idx", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("active_turn", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("turn_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spent_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("llm_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(NOT active_turn) OR turn_started_at IS NOT NULL",
            name=op.f("ck_compass_sessions_turn_started"),
        ),
        sa.CheckConstraint(
            "spent_micros >= 0 AND llm_calls >= 0",
            name=op.f("ck_compass_sessions_spend_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compass_sessions")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_compass_sessions_id_tenant"),
    )
    op.create_index(
        "ix_compass_sessions_active_turn",
        "compass_sessions",
        ["turn_started_at"],
        unique=False,
        postgresql_where=sa.text("active_turn"),
    )
    op.create_index(
        "ix_compass_sessions_tenant_user_updated",
        "compass_sessions",
        ["tenant_id", "user_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "compass_assumptions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("assumption_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_assumptions_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "assumption_id", name=op.f("pk_compass_assumptions")),
    )
    op.create_table(
        "compass_brief_versions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("section_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("baseline_label", sa.Text(), nullable=True),
        sa.Column("change_set_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_compass_brief_versions_version_positive")),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_brief_versions_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "version", name=op.f("pk_compass_brief_versions")),
    )
    op.create_index(
        "uq_compass_brief_versions_baseline",
        "compass_brief_versions",
        ["session_id", "baseline_label"],
        unique=True,
        postgresql_where=sa.text("baseline_label IS NOT NULL"),
    )
    op.create_table(
        "compass_call_log",
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("spec_id", sa.Text(), nullable=False),
        sa.Column("spec_version", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_call_log_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("n", name=op.f("pk_compass_call_log")),
    )
    op.create_index(
        "ix_compass_call_log_session", "compass_call_log", ["session_id", "n"], unique=False
    )
    op.create_table(
        "compass_change_sets",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("change_set_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("new_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_change_sets_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "change_set_id", name=op.f("pk_compass_change_sets")),
    )
    op.create_table(
        "compass_claims",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("claim_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("slot_hint", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_claims_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "claim_id", name=op.f("pk_compass_claims")),
    )
    op.create_table(
        "compass_contributors",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("contributor_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_contributors_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id", "contributor_id", name=op.f("pk_compass_contributors")
        ),
    )
    op.create_table(
        "compass_events",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("search_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(direction = 'out') = (seq IS NOT NULL)",
            name=op.f("ck_compass_events_seq_iff_outbound"),
        ),
        sa.CheckConstraint(
            "direction = 'out' OR event_id IS NOT NULL",
            name=op.f("ck_compass_events_inbound_has_event_id"),
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name=op.f("ck_compass_events_direction")),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_events_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "idx", name=op.f("pk_compass_events")),
    )
    op.create_index(
        "ix_compass_events_turn",
        "compass_events",
        ["session_id", "turn_id"],
        unique=False,
        postgresql_where=sa.text("turn_id IS NOT NULL"),
    )
    op.create_index(
        "uq_compass_events_inbound_event_id",
        "compass_events",
        ["session_id", "event_id"],
        unique=True,
        postgresql_where=sa.text("direction = 'in'"),
    )
    op.create_index(
        "uq_compass_events_session_seq",
        "compass_events",
        ["session_id", "seq"],
        unique=True,
        postgresql_where=sa.text("seq IS NOT NULL"),
    )
    op.create_table(
        "compass_features",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("feature_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scope_status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("family", sa.Text(), nullable=True),
        sa.Column("capability_tier", sa.Text(), nullable=False),
        sa.Column("custom", sa.Boolean(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_features_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "feature_id", name=op.f("pk_compass_features")),
    )
    op.create_table(
        "compass_glossary",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_glossary_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "position", name=op.f("pk_compass_glossary")),
    )
    op.create_table(
        "compass_questions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("card", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("why", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "answer", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('open', 'answered', 'skipped', 'you_decide', 'back')",
            name=op.f("ck_compass_questions_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_questions_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "card_id", name=op.f("pk_compass_questions")),
    )
    op.create_table(
        "compass_research_requests",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("depth", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("user_added", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_research_requests_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id", "request_id", name=op.f("pk_compass_research_requests")
        ),
    )
    op.create_table(
        "compass_slots",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_slots_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "slot_id", name=op.f("pk_compass_slots")),
    )
    op.create_table(
        "compass_sources",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_sources_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "id", name=op.f("pk_compass_sources")),
        sa.UniqueConstraint("session_id", "sha256", name="uq_compass_sources_session_sha256"),
    )
    op.create_table(
        "compass_chunks",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "source_id"],
            ["compass_sources.session_id", "compass_sources.id"],
            name=op.f("fk_compass_chunks_session_id_compass_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["compass_sessions.id", "compass_sessions.tenant_id"],
            name=op.f("fk_compass_chunks_session_id_compass_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id", "source_id", "chunk_id", name=op.f("pk_compass_chunks")
        ),
    )
    op.create_index(
        "ix_compass_chunks_order",
        "compass_chunks",
        ["session_id", "source_id", "idx"],
        unique=False,
    )
    _create_brief_version_guard()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_compass_brief_version_guard ON compass_brief_versions")
    op.execute("DROP FUNCTION IF EXISTS compass_brief_version_guard()")
    op.drop_index("ix_compass_chunks_order", table_name="compass_chunks")
    op.drop_table("compass_chunks")
    op.drop_table("compass_sources")
    op.drop_table("compass_slots")
    op.drop_table("compass_research_requests")
    op.drop_table("compass_questions")
    op.drop_table("compass_glossary")
    op.drop_table("compass_features")
    op.drop_index(
        "uq_compass_events_session_seq",
        table_name="compass_events",
        postgresql_where=sa.text("seq IS NOT NULL"),
    )
    op.drop_index(
        "uq_compass_events_inbound_event_id",
        table_name="compass_events",
        postgresql_where=sa.text("direction = 'in'"),
    )
    op.drop_index(
        "ix_compass_events_turn",
        table_name="compass_events",
        postgresql_where=sa.text("turn_id IS NOT NULL"),
    )
    op.drop_table("compass_events")
    op.drop_table("compass_contributors")
    op.drop_table("compass_claims")
    op.drop_table("compass_change_sets")
    op.drop_index("ix_compass_call_log_session", table_name="compass_call_log")
    op.drop_table("compass_call_log")
    op.drop_index(
        "uq_compass_brief_versions_baseline",
        table_name="compass_brief_versions",
        postgresql_where=sa.text("baseline_label IS NOT NULL"),
    )
    op.drop_table("compass_brief_versions")
    op.drop_table("compass_assumptions")
    op.drop_index("ix_compass_sessions_tenant_user_updated", table_name="compass_sessions")
    op.drop_index(
        "ix_compass_sessions_active_turn",
        table_name="compass_sessions",
        postgresql_where=sa.text("active_turn"),
    )
    op.drop_table("compass_sessions")


def _create_brief_version_guard() -> None:
    """A saved Brief version never changes: the only allowed UPDATE labels it as a baseline, once.
    DELETE is allowed only through the session cascade (the whole session goes away)."""
    op.execute(
        """
        CREATE FUNCTION compass_brief_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.session_id IS DISTINCT FROM OLD.session_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.brief IS DISTINCT FROM OLD.brief
               OR NEW.hash IS DISTINCT FROM OLD.hash
               OR NEW.section_hashes IS DISTINCT FROM OLD.section_hashes
               OR NEW.change_set_id IS DISTINCT FROM OLD.change_set_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'compass_brief_versions: a saved brief version is immutable';
            END IF;
            IF OLD.baseline_label IS NOT NULL
               AND NEW.baseline_label IS DISTINCT FROM OLD.baseline_label THEN
                RAISE EXCEPTION 'compass_brief_versions: an approved baseline is immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_compass_brief_version_guard BEFORE UPDATE ON compass_brief_versions "
        "FOR EACH ROW EXECUTE FUNCTION compass_brief_version_guard()"
    )
