"""Security test fixtures: quiet logs by default, a network guard, and the store matrix."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import structlog

from evals.harness.stack import NetworkGuard, StoreFactory, network_guard


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest.fixture
def guard() -> Iterator[NetworkGuard]:
    """Fails the test if the run tried to reach a non-loopback host."""
    with network_guard() as g:
        yield g
    assert g.attempts == [], f"the run reached the network: {g.attempts}"


@asynccontextmanager
async def _postgres_factory(database: object) -> AsyncIterator[StoreFactory]:
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore
    from mde_persistence.testing_plugin import truncate_all

    truncate_all(database)  # type: ignore[arg-type]
    engine = create_engine(url=database)  # type: ignore[arg-type]
    try:
        yield lambda clock, ids: PostgresStore(engine, clock=clock)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def postgres_store_factory(request: pytest.FixtureRequest) -> AsyncIterator[StoreFactory]:
    """A ``PostgresStore`` factory over the persistence plugin's ephemeral database."""
    async with _postgres_factory(request.getfixturevalue("pg_database")) as factory:
        yield factory


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.integration),
    ]
)
async def store_factory(request: pytest.FixtureRequest) -> AsyncIterator[StoreFactory | None]:
    """``None`` (the in-memory fake) or a ``PostgresStore`` factory: the same test on both."""
    if request.param == "memory":
        yield None
        return
    async with _postgres_factory(request.getfixturevalue("pg_database")) as factory:
        yield factory
