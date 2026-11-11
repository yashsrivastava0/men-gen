"""The migrations: fresh database to head, downgrade to base, no drift from the models, the partial unique index,
the append-only guard, CLI."""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

from mde_research_agent.store.migrate import downgrade, upgrade
from mde_research_agent.store.models import (
    ACTIVE_RUN_INDEX,
    GUARD_FUNCTION,
    VERSION_TABLE,
    Base,
    include_object,
)
from mde_research_agent.store.postgres import normalize_url

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {"rsch_runs", "rsch_events", "rsch_results", "rsch_decisions"}
HEAD = "0001"


def _dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _tables(url: URL) -> set[str]:
    with psycopg.connect(_dsn(url)) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def _functions(url: URL) -> list[str]:
    with psycopg.connect(_dsn(url)) as conn:
        rows = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = %s", (GUARD_FUNCTION,)
        ).fetchall()
    return [r[0] for r in rows]


def _diff(url: URL, **opts: object) -> list[object]:
    engine = create_engine(
        normalize_url(url).render_as_string(hide_password=False), poolclass=pool.NullPool
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
                    **opts,
                },
            )
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def test_model_tables_are_all_prefixed_and_match_the_expected_set() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert all(name.startswith("rsch_") for name in Base.metadata.tables)
    assert VERSION_TABLE == "research_alembic_version"


def test_upgrade_creates_exactly_the_expected_tables(research_empty_database: URL) -> None:
    assert _tables(research_empty_database) == set()
    upgrade(research_empty_database)
    assert _tables(research_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}
    assert _functions(research_empty_database) == [GUARD_FUNCTION]
    upgrade(research_empty_database)  # idempotent
    assert _tables(research_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}
    with psycopg.connect(_dsn(research_empty_database)) as conn:
        assert conn.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchall() == [(HEAD,)]  # noqa: S608


def test_downgrade_to_base_leaves_only_the_version_table(research_empty_database: URL) -> None:
    upgrade(research_empty_database)
    downgrade(research_empty_database, "base")
    assert _tables(research_empty_database) == {VERSION_TABLE}
    assert _functions(research_empty_database) == []  # the trigger function is gone too
    upgrade(research_empty_database)  # and the schema can be rebuilt
    assert _tables(research_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}


def test_downgrade_works_with_rows_in_every_table(research_empty_database: URL) -> None:
    upgrade(research_empty_database)
    with psycopg.connect(_dsn(research_empty_database), autocommit=True) as conn:
        _seed(conn)
    downgrade(research_empty_database, "base")
    assert _tables(research_empty_database) == {VERSION_TABLE}


def test_no_drift_between_the_models_and_the_migrations(research_empty_database: URL) -> None:
    upgrade(research_empty_database)
    assert _diff(research_empty_database) == []


def test_the_drift_check_actually_detects_drift(research_empty_database: URL) -> None:
    """Guard against a vacuous drift test: a dropped index must show up in the comparison."""
    upgrade(research_empty_database)
    with psycopg.connect(_dsn(research_empty_database), autocommit=True) as conn:
        conn.execute("DROP INDEX ix_rsch_decisions_run")
    assert _diff(research_empty_database), "dropping an index must be reported as drift"


def test_other_tables_in_the_database_are_left_alone(research_empty_database: URL) -> None:
    """The migration only manages rsch_* objects: a foreign table survives upgrade and downgrade, and the drift
    check does not report it."""
    with psycopg.connect(_dsn(research_empty_database), autocommit=True) as conn:
        conn.execute("CREATE TABLE compass_sessions (id text PRIMARY KEY)")
    upgrade(research_empty_database)
    assert _diff(research_empty_database) == []
    downgrade(research_empty_database, "base")
    assert "compass_sessions" in _tables(research_empty_database)


def test_running_the_migration_module_as_a_subprocess(research_empty_database: URL) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": normalize_url(research_empty_database).render_as_string(
            hide_password=False
        ),
    }
    done = subprocess.run(
        [sys.executable, "-m", "mde_research_agent.store.migrate"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "upgrade done" in done.stdout
    assert "mde:mde@" not in done.stdout, "the password must not be printed"
    assert _tables(research_empty_database) == EXPECTED_TABLES | {VERSION_TABLE}


# --------------------------------------------------------------------------- SQL-level guarantees


def _run(
    conn: psycopg.Connection, run_id: str, program: str, status: str, tenant: str = "t"
) -> None:
    conn.execute(
        "INSERT INTO rsch_runs (run_id, tenant_id, program_id, status, brief_hash, brief_version, "
        "export_sha256, skip_requested, created_at) VALUES (%s, %s, %s, %s, 'h', 1, '', false, now())",
        (run_id, tenant, program, status),
    )


def _seed(conn: psycopg.Connection, run_id: str = "rrn_a") -> None:
    _run(conn, run_id, "prog_a", "complete")
    conn.execute(
        "INSERT INTO rsch_events (run_id, seq, event, created_at) VALUES (%s, 1, '{}', now())",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO rsch_results (run_id, research, evidence, content_hash, created_at) "
        "VALUES (%s, '{\"items\": []}', '{}', 'h1', now())",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO rsch_decisions (run_id, item_id, kind, reason, at) "
        "VALUES (%s, 'RI-1', 'reject', '', now())",
        (run_id,),
    )


def test_the_partial_unique_index_exists_and_enforces_one_active_run(
    research_empty_database: URL,
) -> None:
    upgrade(research_empty_database)
    with psycopg.connect(_dsn(research_empty_database), autocommit=True) as conn:
        definition = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s", (ACTIVE_RUN_INDEX,)
        ).fetchone()
        assert definition is not None
        text = definition[0]
        assert "UNIQUE" in text and "(tenant_id, program_id)" in text and "WHERE" in text
        for status in ("queued", "running", "committing"):
            assert status in text

        _run(conn, "rrn_1", "prog", "queued")
        for status in ("queued", "running", "committing"):
            with pytest.raises(psycopg.errors.UniqueViolation) as err:
                _run(conn, f"rrn_dup_{status}", "prog", status)
            assert err.value.diag.constraint_name == ACTIVE_RUN_INDEX
        # not active: any number; another program or another tenant: fine
        for status in ("complete", "partial", "failed", "skipped", "interrupted"):
            _run(conn, f"rrn_done_{status}", "prog", status)
        _run(conn, "rrn_other_program", "prog_2", "running")
        _run(conn, "rrn_other_tenant", "prog", "running", tenant="t2")
        # finishing the active run frees the slot
        conn.execute("UPDATE rsch_runs SET status = 'complete' WHERE run_id = 'rrn_1'")
        _run(conn, "rrn_next", "prog", "queued")


def test_events_are_keyed_by_run_and_seq_and_seq_starts_at_one(
    research_empty_database: URL,
) -> None:
    upgrade(research_empty_database)
    with psycopg.connect(_dsn(research_empty_database), autocommit=True) as conn:
        _run(conn, "rrn_1", "prog", "queued")
        insert = (
            "INSERT INTO rsch_events (run_id, seq, event, created_at) VALUES (%s, %s, '{}', now())"
        )
        conn.execute(insert, ("rrn_1", 1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, ("rrn_1", 1))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, ("rrn_1", 0))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(insert, ("rrn_missing", 1))


def test_results_are_immutable_at_the_sql_level(research_empty_database: URL) -> None:
    """No UPDATE of any column and no direct DELETE of a result (nor of an event or a decision); only deleting the
    whole run removes them (the cascade)."""
    upgrade(research_empty_database)
    dsn = _dsn(research_empty_database)
    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed(conn)

    def attempt(statement: str) -> str | None:
        with psycopg.connect(dsn, autocommit=True) as conn:
            try:
                conn.execute(statement)  # type: ignore[arg-type]
            except psycopg.errors.RaiseException as exc:
                return str(exc)
        return None

    for change in (
        "research = '{\"items\": [1]}'",
        "evidence = '{\"x\": 1}'",
        "content_hash = 'other'",
        "created_at = now()",
        "run_id = 'rrn_b'",
    ):
        message = attempt(f"UPDATE rsch_results SET {change}")  # noqa: S608
        assert message is not None and "never updated" in message, change
    for statement in (
        "DELETE FROM rsch_results",
        "DELETE FROM rsch_results WHERE run_id = 'rrn_a'",
    ):
        message = attempt(statement)
        assert message is not None and "never deleted" in message
    # events and decisions are append-only too
    assert attempt("UPDATE rsch_events SET event = '{\"x\": 1}'") is not None
    assert attempt("DELETE FROM rsch_events") is not None
    assert attempt("UPDATE rsch_decisions SET kind = 'accept'") is not None
    assert attempt("DELETE FROM rsch_decisions") is not None

    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT content_hash FROM rsch_results").fetchall() == [("h1",)]
        assert conn.execute("SELECT count(*) FROM rsch_events").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM rsch_decisions").fetchone() == (1,)
        # the run row itself may change (status, hashes) and a second result for the run is refused by the key
        conn.execute("UPDATE rsch_runs SET reason = 'x' WHERE run_id = 'rrn_a'")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO rsch_results (run_id, research, evidence, content_hash, created_at) "
                "VALUES ('rrn_a', '{}', '{}', 'h2', now())"
            )
        # deleting the run is the one way they go away
        conn.execute("DELETE FROM rsch_runs WHERE run_id = 'rrn_a'")
        for table in ("rsch_results", "rsch_events", "rsch_decisions"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608


def test_the_foreign_keys_cascade(research_empty_database: URL) -> None:
    upgrade(research_empty_database)
    with psycopg.connect(_dsn(research_empty_database)) as conn:
        rows = conn.execute(
            "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint "
            "WHERE contype = 'f' AND confrelid = 'rsch_runs'::regclass"
        ).fetchall()
    assert sorted(rows) == [
        ("rsch_decisions", "c"),
        ("rsch_events", "c"),
        ("rsch_results", "c"),
    ]
