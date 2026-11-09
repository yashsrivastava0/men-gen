"""The migrations: fresh database to head, downgrade to base, no drift from the models, CLI."""

from __future__ import annotations

import subprocess
import sys

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

from mde_compass.store.migrate import downgrade, upgrade
from mde_compass.store.models import VERSION_TABLE, Base, include_object
from mde_persistence.db import normalize_url

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "compass_sessions",
    "compass_events",
    "compass_sources",
    "compass_chunks",
    "compass_claims",
    "compass_slots",
    "compass_features",
    "compass_assumptions",
    "compass_research_requests",
    "compass_glossary",
    "compass_questions",
    "compass_brief_versions",
    "compass_contributors",
    "compass_change_sets",
    "compass_call_log",
    # 0002 interview memory (ADR 0015)
    "compass_qa",
    "compass_facts",
    "compass_interview_summary",
    "compass_batches",
}
MEMORY_TABLES = {"compass_qa", "compass_facts", "compass_interview_summary", "compass_batches"}
HEAD = "0003"


def _dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _tables(url: URL) -> set[str]:
    with psycopg.connect(_dsn(url)) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def test_model_tables_are_all_prefixed_and_match_the_expected_set() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert all(name.startswith("compass_") for name in Base.metadata.tables)


def test_upgrade_creates_exactly_the_expected_tables(compass_empty_database: URL) -> None:
    assert _tables(compass_empty_database) == set()
    upgrade(compass_empty_database)
    assert _tables(compass_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}
    upgrade(compass_empty_database)  # idempotent


def test_downgrade_to_base_leaves_only_the_version_table(compass_empty_database: URL) -> None:
    upgrade(compass_empty_database)
    downgrade(compass_empty_database, "base")
    assert _tables(compass_empty_database) == {VERSION_TABLE}
    with psycopg.connect(_dsn(compass_empty_database)) as conn:
        left = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname IN "
            "('compass_brief_version_guard', 'compass_fact_guard')"
        ).fetchall()
    assert left == []  # the trigger functions are gone too
    upgrade(compass_empty_database)  # and the schema can be rebuilt
    assert _tables(compass_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}


def test_no_drift_between_the_models_and_the_migrations(compass_empty_database: URL) -> None:
    upgrade(compass_empty_database)
    engine = create_engine(
        normalize_url(compass_empty_database).render_as_string(hide_password=False),
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "target_metadata": Base.metadata,
                    "include_object": include_object,
                    "compare_type": True,
                    "compare_server_default": True,
                    "version_table": VERSION_TABLE,
                },
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


SESSION_INSERT = (
    "INSERT INTO compass_sessions (id, tenant_id, user_id, mode, locale, state, payload, "
    "created_at, updated_at) VALUES ('ses_m', 't', 'u', 'm', 'en', 'WELCOME', '{}', now(), now())"
)
FACT_INSERT = (
    "INSERT INTO compass_facts (session_id, fact_id, tenant_id, type, statement, topics, "
    "entities, consumers, confidence, status, origin, source_ref, quote, unverified_quote, "
    "supersedes, not_used_reason, effects, aspects, created_at, updated_at) VALUES "
    "('ses_m', 'FCT-001', 't', 'goal', 's', '[]', '[]', '[]', 0.5, 'active', 'stated', '', "
    "'q', false, '[]', '', '[]', '[]', now(), now())"
)


def _version(url: URL) -> str | None:
    with psycopg.connect(_dsn(url)) as conn:
        row = conn.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone()  # noqa: S608
    return None if row is None else str(row[0])


def test_head_is_the_interview_memory_revision(compass_empty_database: URL) -> None:
    upgrade(compass_empty_database)
    assert _version(compass_empty_database) == HEAD


def test_0002_upgrade_downgrade_round_trip_keeps_0001_intact(
    compass_empty_database: URL,
) -> None:
    """0002 adds exactly the four memory tables and the fact guard, and its downgrade removes exactly
    those (even with rows in them), leaving the 0001 schema as it was."""
    url = compass_empty_database
    upgrade(url, "0001")
    assert _tables(url) == (EXPECTED_TABLES - MEMORY_TABLES) | {VERSION_TABLE}
    upgrade(url)
    assert _tables(url) == EXPECTED_TABLES | {VERSION_TABLE}
    with psycopg.connect(_dsn(url), autocommit=True) as conn:
        conn.execute(SESSION_INSERT)
        conn.execute(FACT_INSERT)
    downgrade(url, "0001")
    assert _tables(url) == (EXPECTED_TABLES - MEMORY_TABLES) | {VERSION_TABLE}
    assert _version(url) == "0001"
    with psycopg.connect(_dsn(url)) as conn:
        assert conn.execute("SELECT count(*) FROM compass_sessions").fetchone() == (1,)
        left = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = 'compass_fact_guard'"
        ).fetchall()
        guard = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = 'compass_brief_version_guard'"
        ).fetchall()
    assert left == [] and guard != []
    upgrade(url)  # and it can be applied again
    assert _tables(url) == EXPECTED_TABLES | {VERSION_TABLE}


def test_the_database_never_deletes_a_fact_and_keeps_what_was_said(
    compass_empty_database: URL,
) -> None:
    """The fact guard: DELETE is refused, id/created_at/statement/quote cannot change, the status and
    the other metadata can, and deleting the whole session still removes its facts (cascade)."""
    upgrade(compass_empty_database)
    dsn = _dsn(compass_empty_database)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SESSION_INSERT)
        conn.execute(FACT_INSERT)

    def attempt(statement: str) -> str | None:
        with psycopg.connect(dsn, autocommit=True) as conn:
            try:
                conn.execute(statement)  # type: ignore[arg-type]
            except psycopg.errors.RaiseException as exc:
                return str(exc)
        return None

    refused = attempt("DELETE FROM compass_facts WHERE session_id = 'ses_m'")
    assert refused is not None and "never deleted" in refused
    assert attempt("DELETE FROM compass_facts") is not None
    for change in (
        "statement = 'rewritten'",
        "quote = 'rewritten'",
        "created_at = now() + interval '1 day'",
        "fact_id = 'FCT-002'",
        "tenant_id = 'other'",
        "session_id = 'ses_other'",
    ):
        message = attempt(f"UPDATE compass_facts SET {change} WHERE session_id = 'ses_m'")  # noqa: S608
        assert message is not None and "immutable" in message, change
    for change in (
        "status = 'superseded'",
        "confidence = 0.9, origin = 'tap', unverified_quote = true",
        "supersedes = '[\"FCT-000\"]'::jsonb, not_used_reason = 'x', updated_at = now()",
    ):
        update = f"UPDATE compass_facts SET {change} WHERE session_id = 'ses_m'"  # noqa: S608
        assert attempt(update) is None
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM compass_facts").fetchone() == (1,)
        row = conn.execute("SELECT statement, quote, status FROM compass_facts").fetchone()
        assert row == ("s", "q", "superseded")
        # deleting the whole session is the one way facts go away
        conn.execute("DELETE FROM compass_sessions WHERE id = 'ses_m'")
        assert conn.execute("SELECT count(*) FROM compass_facts").fetchone() == (0,)


def test_the_database_refuses_bad_values_in_the_memory_tables(
    compass_empty_database: URL,
) -> None:
    upgrade(compass_empty_database)
    dsn = _dsn(compass_empty_database)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SESSION_INSERT)

    def refused(statement: str) -> bool:
        with psycopg.connect(dsn, autocommit=True) as conn:
            try:
                conn.execute(statement)  # type: ignore[arg-type]
            except psycopg.errors.IntegrityError:
                return True
        return False

    qa = (
        "INSERT INTO compass_qa (session_id, card_id, version, tenant_id, mode, status, card, "
        "input_mode) VALUES ('ses_m', 'Q-001', {version}, '{tenant}', {mode}, '{status}', '{{}}', "
        "'{input_mode}')"
    )
    ok = {"version": 1, "tenant": "t", "mode": "NULL", "status": "open", "input_mode": "tap"}
    assert not refused(qa.format(**ok))
    assert refused(qa.format(**{**ok, "version": 0}))
    assert refused(qa.format(**{**ok, "version": 2, "status": "gone"}))
    assert refused(qa.format(**{**ok, "version": 2, "mode": "'wander'"}))
    assert refused(qa.format(**{**ok, "version": 2, "input_mode": "telepathy"}))
    assert refused(qa.format(**{**ok, "version": 2, "tenant": "other"}))  # tenant of the session
    assert not refused(qa.format(**{**ok, "version": 2, "mode": "'replan'"}))
    assert refused(FACT_INSERT.replace("'active'", "'deleted'").replace("'FCT-001'", "'FCT-2'"))
    assert refused(FACT_INSERT.replace("'stated'", "'guess'").replace("'FCT-001'", "'FCT-3'"))
    assert refused(FACT_INSERT.replace("0.5", "1.5").replace("'FCT-001'", "'FCT-4'"))
    assert not refused(FACT_INSERT)
    assert refused(FACT_INSERT)  # primary key


def test_the_foreign_keys_cascade_and_name_the_session(compass_empty_database: URL) -> None:
    upgrade(compass_empty_database)
    with psycopg.connect(_dsn(compass_empty_database)) as conn:
        rows = conn.execute(
            "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint "
            "WHERE contype = 'f' AND conrelid::regclass::text = ANY(%s)",
            (sorted(MEMORY_TABLES),),
        ).fetchall()
    assert sorted(rows) == [(table, "c") for table in sorted(MEMORY_TABLES)]


def test_other_tables_in_the_database_are_left_alone(compass_empty_database: URL) -> None:
    """The migration only manages compass_* objects: a foreign table survives upgrade and downgrade."""
    with psycopg.connect(_dsn(compass_empty_database), autocommit=True) as conn:
        conn.execute("CREATE TABLE session (id text PRIMARY KEY)")
    upgrade(compass_empty_database)
    downgrade(compass_empty_database, "base")
    assert "session" in _tables(compass_empty_database)


def test_running_the_migration_module_as_a_subprocess(
    compass_empty_database: URL,
) -> None:
    import os

    env = {
        **os.environ,
        "DATABASE_URL": normalize_url(compass_empty_database).render_as_string(hide_password=False),
    }
    done = subprocess.run(
        [sys.executable, "-m", "mde_compass.store.migrate"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "upgrade done" in done.stdout
    assert _tables(compass_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}


def test_the_drift_check_actually_detects_drift(compass_empty_database: URL) -> None:
    """Guard against a vacuous drift test: a dropped index must show up in the comparison."""
    upgrade(compass_empty_database)
    with psycopg.connect(_dsn(compass_empty_database), autocommit=True) as conn:
        conn.execute("DROP INDEX ix_compass_chunks_order")
    engine = create_engine(
        normalize_url(compass_empty_database).render_as_string(hide_password=False),
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"target_metadata": Base.metadata, "include_object": include_object},
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff, "dropping an index must be reported as drift"
