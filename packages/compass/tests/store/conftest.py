"""Fixtures for the store tests: a session-scoped ephemeral database (``mde_test_compass_<ulid>``),
function-scoped truncated engines and stores. Registered here, NOT through the root conftest's
``pytest_plugins``. An unreachable PostgreSQL is a FAILURE, never a skip."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_compass.store.memory import InMemoryStore
from mde_compass.store.postgres import PostgresStore, create_store_engine
from mde_compass.store.testing import (
    PostgresUnavailable,
    admin_dsn,
    drop_stale_databases,
    ensure_reachable,
    ephemeral_database,
    truncate_all,
)
from mde_core.clock import FakeClock


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def memory_store(fake_clock: FakeClock) -> InMemoryStore:
    return InMemoryStore(fake_clock)


@pytest.fixture(scope="session")
def compass_admin_dsn() -> str:
    dsn = admin_dsn()
    try:
        ensure_reachable(dsn)
    except PostgresUnavailable as exc:
        pytest.fail(str(exc), pytrace=False)
    return dsn


@pytest.fixture(scope="session")
def compass_database(compass_admin_dsn: str) -> Iterator[URL]:
    """SQLAlchemy URL of this test session's ephemeral, migrated database."""
    drop_stale_databases(compass_admin_dsn)
    with ephemeral_database(compass_admin_dsn) as url:
        yield url


@pytest.fixture
def compass_empty_database(compass_admin_dsn: str) -> Iterator[URL]:
    """A brand-new database with NO tables (migration tests)."""
    with ephemeral_database(compass_admin_dsn, migrate=False) as url:
        yield url


@pytest_asyncio.fixture
async def compass_engine(compass_database: URL) -> AsyncIterator[AsyncEngine]:
    truncate_all(compass_database)
    engine = create_store_engine(compass_database)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def postgres_store(compass_engine: AsyncEngine, fake_clock: FakeClock) -> PostgresStore:
    return PostgresStore(compass_engine, clock=fake_clock)


@pytest.fixture
def make_postgres_store(compass_engine: AsyncEngine) -> Callable[..., PostgresStore]:
    def factory(*, clock: FakeClock | None = None) -> PostgresStore:
        return PostgresStore(compass_engine, clock=clock)

    return factory
