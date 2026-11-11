"""Test helpers for the Research store (a real package module: pytest's importlib mode forbids test-to-test
imports).

* Ephemeral PostgreSQL databases ``mde_test_research_<ulid>``: create, migrate with ``store.migrate.upgrade``, drop
  with ``WITH (FORCE)``; stale ones (older than six hours, by the time inside the ULID) are dropped on the way in.
  Tests never touch the dev database. An unreachable PostgreSQL raises ``PostgresUnavailable``; the pytest fixtures
  turn it into a FAILURE, never a skip. The server is the private cluster on port 54329 (override the admin URL
  with ``MDE_TEST_PG_URL``); never port 5432.
* ``temporary_postgres_store``: a ready ``PostgresStore`` over a throw-away database.
* ``truncate_all``: empties the ``rsch_*`` tables only.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import AsyncIterator, Iterator

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from mde_core.clock import Clock
from mde_core.ids import new_ulid
from mde_research_agent.store.migrate import upgrade
from mde_research_agent.store.models import Base
from mde_research_agent.store.postgres import (
    PostgresStore,
    create_store_engine,
    ensure_selector_loop_policy,
    redact_url,
)

DEFAULT_ADMIN_URL = "postgresql://mde:mde@127.0.0.1:54329/postgres"
TEST_DB_PREFIX = "mde_test_research_"
STALE_AFTER_SECONDS = 6 * 3600
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ensure_selector_loop_policy()  # psycopg async needs a selector loop on Windows


class PostgresUnavailable(RuntimeError):
    """The test PostgreSQL server cannot be reached (integration tests never skip)."""


# --------------------------------------------------------------------------- databases


def admin_dsn() -> str:
    """libpq-style DSN of the server, from ``MDE_TEST_PG_URL`` or the private cluster default."""
    raw = os.environ.get("MDE_TEST_PG_URL") or DEFAULT_ADMIN_URL
    return make_url(raw).set(drivername="postgresql").render_as_string(hide_password=False)


def ensure_reachable(dsn: str) -> None:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except psycopg.Error as exc:
        raise PostgresUnavailable(
            f"PostgreSQL is not reachable at {redact_url(dsn)} ({type(exc).__name__}). "
            "Integration tests never skip: start it with: just db-up"
        ) from exc


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


def drop_stale_databases(dsn: str) -> None:
    """Drop ``mde_test_research_*`` databases of crashed runs (best effort, never fails a run)."""
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
        pass


def drop_database(dsn: str, name: str) -> None:
    assert name.startswith(TEST_DB_PREFIX), f"refusing to drop {name!r}"
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(_drop_sql(name))


@contextlib.contextmanager
def ephemeral_database(admin: str | None = None, *, migrate: bool = True) -> Iterator[URL]:
    """A fresh database ``mde_test_research_<ulid>`` (migrated to head unless ``migrate=False``); yields its
    SQLAlchemy URL (``postgresql+psycopg``) and drops it afterwards."""
    server = admin or admin_dsn()
    name = TEST_DB_PREFIX + new_ulid().lower()
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(server).set(drivername="postgresql+psycopg", database=name)
    try:
        if migrate:
            upgrade(url)
        yield url
    finally:
        drop_database(server, name)


def truncate_all(url: str | URL) -> None:
    """Empty every Research table of a test database (synchronous, loop-independent)."""
    parsed = make_url(url) if isinstance(url, str) else url
    dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    tables = sql.SQL(", ").join(sql.Identifier(t.name) for t in Base.metadata.sorted_tables)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(tables))


@contextlib.asynccontextmanager
async def temporary_postgres_store(clock: Clock | None = None) -> AsyncIterator[PostgresStore]:
    """A ``PostgresStore`` over its own throw-away migrated database (for API and service tests that want the
    real store). Fails with ``PostgresUnavailable`` when the server is down; drops the database and disposes the
    pool on exit."""
    ensure_reachable(admin_dsn())
    with ephemeral_database() as url:
        engine = create_store_engine(url)
        try:
            yield PostgresStore(engine, clock=clock)
        finally:
            await engine.dispose()
