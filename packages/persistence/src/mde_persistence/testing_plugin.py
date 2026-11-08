"""pytest plugin: an ephemeral PostgreSQL database per test session (brief 9.1).

Registered automatically by the root ``conftest.py`` when this package is importable.

* ``pg_admin_url`` (session): server URL, env ``MDE_TEST_PG_URL`` (default: the private cluster on
  ``127.0.0.1:54329``). If it is unreachable the test FAILS LOUDLY; integration tests never skip.
* ``pg_database`` (session): a fresh database ``mde_test_<ulid>`` with migrations applied; dropped
  (``WITH (FORCE)``) at the end. Tests never touch the dev database ``mde``. Names are unique, so
  agents running tests against the same server concurrently do not collide; stale databases of
  crashed runs (older than six hours, by the time inside their ULID) are dropped on the way in.
* ``empty_database`` (function): a database with no tables, for migration tests.
* ``pg_dsn`` (session): libpq DSN of the migrated database, for raw SQL in schema tests.
* ``pg_engine`` / ``store`` / ``make_store`` (function): tables are truncated first (fast), the
  async engine is created inside the test's event loop and disposed afterwards.
* ``fake_clock``: a ``FakeClock`` for time-dependent queue tests.

Mark database tests ``@pytest.mark.integration``.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_core.clock import Clock, FakeClock
from mde_core.ids import new_ulid
from mde_persistence.db import create_engine, ensure_selector_loop_policy, redact_url
from mde_persistence.migrate import upgrade
from mde_persistence.models import Base
from mde_persistence.store import PostgresStore

DEFAULT_ADMIN_URL = "postgresql://mde:mde@127.0.0.1:54329/postgres"
TEST_DB_PREFIX = "mde_test_"
STALE_AFTER_SECONDS = 6 * 3600
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ensure_selector_loop_policy()  # psycopg async needs a selector loop on Windows


def admin_dsn() -> str:
    """libpq-style DSN of the server (``postgresql://``), from ``MDE_TEST_PG_URL`` or the default."""
    raw = os.environ.get("MDE_TEST_PG_URL") or DEFAULT_ADMIN_URL
    return make_url(raw).set(drivername="postgresql").render_as_string(hide_password=False)


def _ulid_ms(text: str) -> int | None:
    head = text[:10].upper()
    if len(text) != 26 or any(ch not in _CROCKFORD for ch in head):
        return None
    value = 0
    for ch in head:
        value = value * 32 + _CROCKFORD.index(ch)
    return value


def _drop_sql(name: str) -> sql.Composed:
    return sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))


def _drop_stale_databases(dsn: str) -> None:
    now_ms = int(time.time() * 1000)
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (TEST_DB_PREFIX + "%",)
            ).fetchall()
            for (name,) in rows:
                created = _ulid_ms(name[len(TEST_DB_PREFIX) :])
                if created is not None and now_ms - created > STALE_AFTER_SECONDS * 1000:
                    conn.execute(_drop_sql(name))
    except psycopg.Error:
        pass  # best effort: housekeeping must never fail a test run


def _drop_database(dsn: str, name: str) -> None:
    assert name.startswith(TEST_DB_PREFIX), f"refusing to drop {name!r}"
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(_drop_sql(name))


def truncate_all(url: str | URL) -> None:
    """Empty every table of the test database (synchronous, loop-independent)."""
    parsed = make_url(url) if isinstance(url, str) else url
    dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    tables = sql.SQL(", ").join(sql.Identifier(t.name) for t in Base.metadata.sorted_tables)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(tables))


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    dsn = admin_dsn()
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except psycopg.Error as exc:
        pytest.fail(
            f"PostgreSQL is not reachable at {redact_url(dsn)} ({type(exc).__name__}). "
            "Integration tests never skip: start it with: just db-up",
            pytrace=False,
        )
    return dsn


@contextlib.contextmanager
def _ephemeral_database(admin: str, *, migrate: bool) -> Iterator[URL]:
    name = TEST_DB_PREFIX + new_ulid().lower()
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(admin).set(drivername="postgresql+psycopg", database=name)
    try:
        if migrate:
            upgrade(url)
        yield url
    finally:
        _drop_database(admin, name)


@pytest.fixture(scope="session")
def pg_database(pg_admin_url: str) -> Iterator[URL]:
    """SQLAlchemy URL of this session's ephemeral, migrated database."""
    _drop_stale_databases(pg_admin_url)
    with _ephemeral_database(pg_admin_url, migrate=True) as url:
        yield url


@pytest.fixture
def empty_database(pg_admin_url: str) -> Iterator[URL]:
    """A brand-new database with NO tables (for migration tests); dropped afterwards."""
    with _ephemeral_database(pg_admin_url, migrate=False) as url:
        yield url


@pytest.fixture
def pg_dsn(pg_database: URL) -> str:
    """libpq DSN of the session database, for raw synchronous SQL in schema tests."""
    return pg_database.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest_asyncio.fixture
async def pg_engine(pg_database: URL) -> AsyncIterator[AsyncEngine]:
    truncate_all(pg_database)
    engine = create_engine(url=pg_database)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def store(pg_engine: AsyncEngine) -> PostgresStore:
    return PostgresStore(pg_engine)


@pytest.fixture
def make_store(pg_engine: AsyncEngine) -> Callable[..., PostgresStore]:
    """``make_store(clock=..., max_attempts=...)``: stores over the same clean database."""

    def factory(*, clock: Clock | None = None, max_attempts: int = 3) -> PostgresStore:
        return PostgresStore(pg_engine, clock=clock, max_attempts=max_attempts)

    return factory
