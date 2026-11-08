"""initial schema: every table of the Context Engine, the queue constraints and the spec guard

Revision ID: 0001
Revises:
Create Date: 2026-11-08 00:00:00+00:00

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
        "llm_call",
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("spec_id", sa.Text(), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("repaired", sa.Boolean(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("cassette_key", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("call_id", name=op.f("pk_llm_call")),
    )
    op.create_index("ix_llm_call_scope", "llm_call", ["scope"], unique=False)
    op.create_index("ix_llm_call_session_at", "llm_call", ["session_id", "at"], unique=False)
    op.create_table(
        "tenant",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
    )
    op.create_table(
        "memory_item",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('user', 'org')", name=op.f("ck_memory_item_scope")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_memory_item_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "scope", "owner_id", "key", name=op.f("pk_memory_item")
        ),
    )
    op.create_table(
        "org_profile",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_org_profile_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_org_profile")),
    )
    op.create_table(
        "policy",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_policy_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_policy")),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("plan_phase", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("paused_from", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("discovery", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=True),
        sa.Column("approved_brief_version", sa.Integer(), nullable=True),
        sa.Column("research_version", sa.Integer(), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=True),
        sa.Column("handed_off_version", sa.Integer(), nullable=True),
        sa.Column("queued_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("outbound_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_session_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_session_id_tenant"),
    )
    op.create_index("ix_session_tenant_app", "session", ["tenant_id", "app_id"], unique=False)
    op.create_index(
        "ix_session_tenant_owner_updated",
        "session",
        ["tenant_id", "owner_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "approval",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("approver_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_approval_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
    )
    op.create_index(
        "ix_approval_session_gate_version",
        "approval",
        ["session_id", "gate", "version"],
        unique=False,
    )
    op.create_table(
        "brief_version",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')", name=op.f("ck_brief_version_status")
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_brief_version_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "version", name=op.f("pk_brief_version")),
    )
    op.create_table(
        "card_instance",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("instance_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("card_type", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("generated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "answer", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("answered_event_id", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_card_instance_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "instance_id", name=op.f("pk_card_instance")),
    )
    op.create_index(
        "ix_card_instance_session_card", "card_instance", ["session_id", "card_id"], unique=False
    )
    op.create_table(
        "job",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("lock_owner", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('plan')", name=op.f("ck_job_kind")),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_job_state"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_job_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
    )
    op.create_index(
        "ix_job_queued_created",
        "job",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("state = 'queued'"),
    )
    op.create_index(
        "ix_job_running_heartbeat",
        "job",
        ["heartbeat_at"],
        unique=False,
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index("ix_job_session_created", "job", ["session_id", "created_at"], unique=False)
    op.create_index("ix_job_state_created", "job", ["state", "created_at"], unique=False)
    op.create_index(
        "uq_job_one_active_per_session",
        "job",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_table(
        "research_note",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("brief_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("depth", sa.Text(), nullable=False),
        sa.Column("note", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_research_note_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "version", name=op.f("pk_research_note")),
    )
    op.create_table(
        "session_event",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(direction = 'out') = (seq IS NOT NULL)",
            name=op.f("ck_session_event_seq_iff_outbound"),
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name=op.f("ck_session_event_direction")),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_session_event_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "event_id", name=op.f("pk_session_event")),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_event_session_seq"),
    )
    op.create_index(
        "ix_session_event_reply",
        "session_event",
        ["session_id", "in_reply_to"],
        unique=False,
        postgresql_where=sa.text("in_reply_to IS NOT NULL"),
    )
    op.create_table(
        "slot",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column(
            "value", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_slot_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "slot_id", name=op.f("pk_slot")),
    )
    op.create_table(
        "spec_version",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("spec_text", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "plan", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.Column("research_version", sa.Integer(), nullable=True),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'handed_off', 'superseded')",
            name=op.f("ck_spec_version_status"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["session.id", "session.tenant_id"],
            name=op.f("fk_spec_version_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "version", name=op.f("pk_spec_version")),
    )
    op.create_index(
        "ix_spec_version_session_status", "spec_version", ["session_id", "status"], unique=False
    )
    op.create_table(
        "research_fact",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("note_version", sa.Integer(), nullable=False),
        sa.Column("fact_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("primary_url", sa.Text(), nullable=True),
        sa.Column("retrieved_on", sa.Date(), nullable=True),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "note_version"],
            ["research_note.session_id", "research_note.version"],
            name=op.f("fk_research_fact_session_id_research_note"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id", "note_version", "fact_id", name=op.f("pk_research_fact")
        ),
    )
    op.create_index(
        "ix_research_fact_session_job", "research_fact", ["session_id", "job"], unique=False
    )
    op.create_table(
        "research_job",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("note_version", sa.Integer(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "note_version"],
            ["research_note.session_id", "research_note.version"],
            name=op.f("fk_research_job_session_id_research_note"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "note_version", "job", name=op.f("pk_research_job")),
    )

    # --- spec_version guard (hard rule 6: approved versions are immutable) -------------------
    # Content columns never change after insert; status moves only along
    # draft -> approved | superseded and approved -> handed_off | superseded; git_sha and
    # approved_at may be set once. Non-draft rows cannot be deleted.
    op.execute(
        """
        CREATE FUNCTION spec_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'spec_version %/% is %: it cannot be deleted',
                        OLD.session_id, OLD.version, OLD.status
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.session_id IS DISTINCT FROM OLD.session_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.hash IS DISTINCT FROM OLD.hash
               OR NEW.spec_text IS DISTINCT FROM OLD.spec_text
               OR NEW.spec IS DISTINCT FROM OLD.spec
               OR NEW.plan IS DISTINCT FROM OLD.plan
               OR NEW.brief_version IS DISTINCT FROM OLD.brief_version
               OR NEW.research_version IS DISTINCT FROM OLD.research_version
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'spec_version %/% content is immutable', OLD.session_id, OLD.version
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.git_sha IS NOT NULL AND NEW.git_sha IS DISTINCT FROM OLD.git_sha THEN
                RAISE EXCEPTION 'spec_version %/% git_sha is already set', OLD.session_id, OLD.version
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.approved_at IS NOT NULL AND NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION 'spec_version %/% approved_at is already set',
                    OLD.session_id, OLD.version
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'draft' AND NEW.status IN ('approved', 'superseded'))
                OR (OLD.status = 'approved' AND NEW.status IN ('handed_off', 'superseded'))
            ) THEN
                RAISE EXCEPTION 'spec_version %/%: illegal status move % -> %',
                    OLD.session_id, OLD.version, OLD.status, NEW.status
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_spec_version_guard BEFORE UPDATE OR DELETE ON spec_version "
        "FOR EACH ROW EXECUTE FUNCTION spec_version_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_spec_version_guard ON spec_version")
    op.execute("DROP FUNCTION IF EXISTS spec_version_guard()")
    op.drop_table("research_job")
    op.drop_index("ix_research_fact_session_job", table_name="research_fact")
    op.drop_table("research_fact")
    op.drop_index("ix_spec_version_session_status", table_name="spec_version")
    op.drop_table("spec_version")
    op.drop_table("slot")
    op.drop_index(
        "ix_session_event_reply",
        table_name="session_event",
        postgresql_where=sa.text("in_reply_to IS NOT NULL"),
    )
    op.drop_table("session_event")
    op.drop_table("research_note")
    op.drop_index(
        "uq_job_one_active_per_session",
        table_name="job",
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.drop_index("ix_job_state_created", table_name="job")
    op.drop_index("ix_job_session_created", table_name="job")
    op.drop_index(
        "ix_job_running_heartbeat", table_name="job", postgresql_where=sa.text("state = 'running'")
    )
    op.drop_index(
        "ix_job_queued_created", table_name="job", postgresql_where=sa.text("state = 'queued'")
    )
    op.drop_table("job")
    op.drop_index("ix_card_instance_session_card", table_name="card_instance")
    op.drop_table("card_instance")
    op.drop_table("brief_version")
    op.drop_index("ix_approval_session_gate_version", table_name="approval")
    op.drop_table("approval")
    op.drop_index("ix_session_tenant_owner_updated", table_name="session")
    op.drop_index("ix_session_tenant_app", table_name="session")
    op.drop_table("session")
    op.drop_table("policy")
    op.drop_table("org_profile")
    op.drop_table("memory_item")
    op.drop_table("tenant")
    op.drop_index("ix_llm_call_session_at", table_name="llm_call")
    op.drop_index("ix_llm_call_scope", table_name="llm_call")
    op.drop_table("llm_call")
