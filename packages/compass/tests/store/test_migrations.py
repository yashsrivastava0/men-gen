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
}


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
            "SELECT proname FROM pg_proc WHERE proname = 'compass_brief_version_guard'"
        ).fetchall()
    assert left == []  # the trigger function is gone too
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
