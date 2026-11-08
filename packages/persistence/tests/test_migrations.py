"""Alembic: the migration builds the schema, the models match it, the CLI path works."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from mde_persistence.migrate import MIGRATIONS_DIR, alembic_config, downgrade, upgrade
from mde_persistence.models import Base

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_TABLES = {
    "tenant",
    "session",
    "session_event",
    "slot",
    "card_instance",
    "brief_version",
    "research_note",
    "research_job",
    "research_fact",
    "spec_version",
    "approval",
    "memory_item",
    "org_profile",
    "policy",
    "llm_call",
    "job",
}


def _tables(url: URL) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_there_is_one_linear_history() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql+psycopg://x/y"))
    assert script.get_heads() == ["0001"]
    assert (MIGRATIONS_DIR / "versions" / "0001_initial_schema.py").exists()


def test_upgrade_builds_every_table_on_an_empty_database(empty_database: URL) -> None:
    assert _tables(empty_database) == set()
    upgrade(empty_database)
    assert _tables(empty_database) == EXPECTED_TABLES | {"alembic_version"}
    engine = create_engine(empty_database)
    try:
        inspector = inspect(engine)
        indexes = {i["name"]: i for i in inspector.get_indexes("job")}
        one_active = indexes["uq_job_one_active_per_session"]
        assert one_active["unique"] is True
        assert one_active["column_names"] == ["session_id"]
        assert "queued" in str(one_active.get("dialect_options", {}).get("postgresql_where"))
        assert {"ix_job_queued_created", "ix_job_running_heartbeat"} <= set(indexes)
        events = inspector.get_unique_constraints("session_event")
        assert any(u["column_names"] == ["session_id", "seq"] for u in events)
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
            )
            triggers = conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            ).scalars()
            assert "trg_spec_version_guard" in set(triggers)
    finally:
        engine.dispose()


def test_models_match_the_migrations(pg_database: URL) -> None:
    """Drift test: autogenerate against the migrated database must find nothing to change."""
    engine = create_engine(pg_database)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_and_upgrade_round_trip(empty_database: URL) -> None:
    upgrade(empty_database)
    downgrade(empty_database, "base")
    assert _tables(empty_database) == {"alembic_version"}
    engine = create_engine(empty_database)
    try:
        with engine.connect() as conn:
            leftovers = conn.execute(
                text("SELECT proname FROM pg_proc WHERE proname = 'spec_version_guard'")
            ).all()
            assert leftovers == []
    finally:
        engine.dispose()
    upgrade(empty_database)
    assert _tables(empty_database) == EXPECTED_TABLES | {"alembic_version"}


def test_upgrade_is_idempotent(empty_database: URL) -> None:
    upgrade(empty_database)
    upgrade(empty_database)  # already at head: nothing to do, no error
    assert _tables(empty_database) >= EXPECTED_TABLES


def test_just_migrate_command_reads_database_url_from_settings(empty_database: URL) -> None:
    """``just migrate`` runs this exact command from the repo root; the URL comes from settings."""
    env = {
        **os.environ,
        "DATABASE_URL": empty_database.render_as_string(hide_password=False),
        "MDE_ENV_FILE": "",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "packages/persistence/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert _tables(empty_database) == EXPECTED_TABLES | {"alembic_version"}


def test_offline_sql_mode_renders_the_schema(empty_database: URL) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": empty_database.render_as_string(hide_password=False),
        "MDE_ENV_FILE": "",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "packages/persistence/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "CREATE TABLE job" in done.stdout
    assert "uq_job_one_active_per_session" in done.stdout
    assert _tables(empty_database) == set()  # offline mode never touches the database
