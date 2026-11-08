"""Business rules that live in the schema itself (raw SQL, bypassing the repositories)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_dsn: str, pg_engine: object) -> Iterator[psycopg.Connection]:
    """A synchronous connection on the clean test database (autocommit: each statement alone)."""
    del pg_engine  # requested only for its TRUNCATE-before-test side effect
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        yield conn


def _tenant(db: psycopg.Connection, tenant: str) -> None:
    db.execute("INSERT INTO tenant (id, created_at) VALUES (%s, %s)", (tenant, NOW))


def _session(db: psycopg.Connection, sid: str, tenant: str) -> None:
    db.execute(
        "INSERT INTO session (id, tenant_id, owner_id, app_id, mode, policy_id, state, language, "
        "discovery, queued_messages, created_at, updated_at) "
        "VALUES (%s, %s, 'u', 'app_1', 'guided', 'p', 'DISCOVERING', 'en', %s, %s, %s, %s)",
        (sid, tenant, Jsonb({}), Jsonb([]), NOW, NOW),
    )


def _job(db: psycopg.Connection, jid: str, sid: str, tenant: str, state: str) -> None:
    db.execute(
        "INSERT INTO job (id, session_id, tenant_id, kind, state, payload, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'plan', %s, %s, %s, %s)",
        (jid, sid, tenant, state, Jsonb({}), NOW, NOW),
    )


def _event(
    db: psycopg.Connection, sid: str, tenant: str, eid: str, direction: str, seq: int | None
) -> None:
    db.execute(
        "INSERT INTO session_event (session_id, event_id, tenant_id, direction, type, seq, payload, "
        "at, recorded_at) VALUES (%s, %s, %s, %s, 't', %s, %s, %s, %s)",
        (sid, eid, tenant, direction, seq, Jsonb({}), NOW, NOW),
    )


def _spec(
    db: psycopg.Connection, sid: str, tenant: str, version: int = 1, status: str = "draft"
) -> None:
    db.execute(
        "INSERT INTO spec_version (session_id, version, tenant_id, hash, status, spec_text, spec, "
        "brief_version, created_at, updated_at) VALUES (%s, %s, %s, 'h', %s, 'text', %s, 1, %s, %s)",
        (sid, version, tenant, status, Jsonb({"a": 1}), NOW, NOW),
    )


def test_child_rows_must_carry_their_sessions_tenant(db: psycopg.Connection) -> None:
    _tenant(db, "tnt_a")
    _tenant(db, "tnt_b")
    _session(db, "ses_1", "tnt_a")
    with pytest.raises(errors.ForeignKeyViolation):
        _job(db, "job_1", "ses_1", "tnt_b", "queued")  # right session, wrong tenant
    with pytest.raises(errors.ForeignKeyViolation):
        _event(db, "ses_1", "tnt_b", "evt_1", "in", None)
    with pytest.raises(errors.ForeignKeyViolation):
        _spec(db, "ses_1", "tnt_b")
    with pytest.raises(errors.ForeignKeyViolation):
        _job(db, "job_2", "ses_missing", "tnt_a", "queued")


def test_one_active_job_per_session_is_a_unique_partial_index(db: psycopg.Connection) -> None:
    _tenant(db, "tnt_a")
    _session(db, "ses_1", "tnt_a")
    _session(db, "ses_2", "tnt_a")
    _job(db, "job_1", "ses_1", "tnt_a", "queued")
    with pytest.raises(errors.UniqueViolation):
        _job(db, "job_2", "ses_1", "tnt_a", "running")  # a second active run
    _job(db, "job_3", "ses_2", "tnt_a", "running")  # another session is independent
    db.execute("UPDATE job SET state = 'succeeded' WHERE id = 'job_1'")
    _job(db, "job_4", "ses_1", "tnt_a", "queued")  # finished runs free the slot
    _job(db, "job_5", "ses_1", "tnt_a", "failed")  # any number of finished runs
    _job(db, "job_6", "ses_1", "tnt_a", "cancelled")
    with pytest.raises(errors.CheckViolation):
        _job(db, "job_7", "ses_2", "tnt_a", "paused")


def test_events_are_unique_per_session_and_seq_only_for_outbound(db: psycopg.Connection) -> None:
    _tenant(db, "tnt_a")
    _session(db, "ses_1", "tnt_a")
    _session(db, "ses_2", "tnt_a")
    _event(db, "ses_1", "tnt_a", "evt_1", "in", None)
    with pytest.raises(errors.UniqueViolation):
        _event(db, "ses_1", "tnt_a", "evt_1", "in", None)  # the idempotency guarantee
    _event(db, "ses_2", "tnt_a", "evt_1", "in", None)  # same id in another session is fine
    _event(db, "ses_1", "tnt_a", "evt_2", "in", None)  # many inbound rows without seq
    _event(db, "ses_1", "tnt_a", "evt_3", "out", 1)
    with pytest.raises(errors.UniqueViolation):
        _event(db, "ses_1", "tnt_a", "evt_4", "out", 1)  # duplicate seq in a session
    _event(db, "ses_2", "tnt_a", "evt_5", "out", 1)
    with pytest.raises(errors.CheckViolation):
        _event(db, "ses_1", "tnt_a", "evt_6", "out", None)  # outbound needs a seq
    with pytest.raises(errors.CheckViolation):
        _event(db, "ses_1", "tnt_a", "evt_7", "in", 9)  # inbound has none
    with pytest.raises(errors.CheckViolation):
        _event(db, "ses_1", "tnt_a", "evt_8", "sideways", None)


def test_spec_version_trigger_makes_content_immutable(db: psycopg.Connection) -> None:
    _tenant(db, "tnt_a")
    _session(db, "ses_1", "tnt_a")
    _spec(db, "ses_1", "tnt_a")
    for statement in (
        "UPDATE spec_version SET spec_text = 'edited'",
        "UPDATE spec_version SET hash = 'other'",
        "UPDATE spec_version SET brief_version = 2",
        "UPDATE spec_version SET research_version = 3",
        "UPDATE spec_version SET version = 9",
    ):
        with pytest.raises(errors.IntegrityConstraintViolation, match="immutable"):
            db.execute(statement)  # type: ignore[arg-type]
    with pytest.raises(errors.IntegrityConstraintViolation, match="immutable"):
        db.execute("UPDATE spec_version SET spec = %s", (Jsonb({"a": 2}),))
    db.execute("UPDATE spec_version SET status = 'approved', approved_at = now()")  # legal move
    with pytest.raises(errors.IntegrityConstraintViolation, match="illegal status move"):
        db.execute("UPDATE spec_version SET status = 'draft'")
    with pytest.raises(errors.IntegrityConstraintViolation, match="approved_at"):
        db.execute("UPDATE spec_version SET approved_at = now() + interval '1 day'")
    db.execute("UPDATE spec_version SET status = 'handed_off', git_sha = 'abc'")
    with pytest.raises(errors.IntegrityConstraintViolation, match="git_sha"):
        db.execute("UPDATE spec_version SET git_sha = 'def'")
    with pytest.raises(errors.IntegrityConstraintViolation, match="illegal status move"):
        db.execute("UPDATE spec_version SET status = 'superseded'")  # handed_off is final
    with pytest.raises(errors.IntegrityConstraintViolation, match="cannot be deleted"):
        db.execute("DELETE FROM spec_version")
    row = db.execute("SELECT status, spec_text, git_sha FROM spec_version").fetchone()
    assert row == ("handed_off", "text", "abc")


def test_spec_version_status_moves_are_checked_in_the_database(db: psycopg.Connection) -> None:
    _tenant(db, "tnt_a")
    _session(db, "ses_1", "tnt_a")
    _spec(db, "ses_1", "tnt_a", 1)
    with pytest.raises(errors.IntegrityConstraintViolation, match="illegal status move"):
        db.execute("UPDATE spec_version SET status = 'handed_off'")  # draft cannot skip approval
    db.execute("UPDATE spec_version SET status = 'superseded'")
    with pytest.raises(errors.IntegrityConstraintViolation, match="illegal status move"):
        db.execute("UPDATE spec_version SET status = 'approved'")
    with pytest.raises(errors.CheckViolation):
        _spec(db, "ses_1", "tnt_a", 3, status="frozen")
    _spec(db, "ses_1", "tnt_a", 2)
    db.execute("DELETE FROM spec_version WHERE version = 2")  # drafts may be deleted
