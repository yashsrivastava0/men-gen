"""Flow tests: quiet structured logging, and the store matrix (in-memory fake / Postgres).

Logging: the service logs expected engine failures with ``log.exception``; structlog's development
renderer pretty-prints each traceback with ``rich`` (about 100 ms), which is painful in a property
test that provokes thousands of them. Errors are asserted on through events and state, not logs.

Store matrix: ``flow_store`` yields a factory ``(clock, ids) -> Store``. Scenario modules opt in by
installing it for every ``make_flow_kit()`` (see ``test_scenarios.py``), so the same scenario runs
over ``InMemoryStore`` and over ``PostgresStore`` (``@pytest.mark.integration``: the local server must
be up, ``uv run python scripts/dev_db.py up``; the persistence plugin creates an ephemeral
database, truncated per test, and never touches the dev database ``mde``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import structlog

from mde_flow.testing.kit import StoreFactory, in_memory_store, use_store_factory


@pytest.fixture(autouse=True, scope="module")
def _quiet_structlog() -> Iterator[None]:
    """Quiet logging for one test module, then restore structlog's defaults.

    It must NOT be session-scoped or left configured: a lingering CRITICAL filter silently drops the
    warning/error logs that other packages' tests capture (``structlog.testing.capture_logs``).
    """
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.integration),
    ]
)
async def flow_store(request: pytest.FixtureRequest) -> AsyncIterator[StoreFactory]:
    """A store factory for ``make_flow_kit``: the in-memory fake, or ``PostgresStore``."""
    if request.param == "memory":
        yield in_memory_store
        return
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore
    from mde_persistence.testing_plugin import truncate_all

    database = request.getfixturevalue("pg_database")  # session fixture of the persistence plugin
    truncate_all(database)
    engine = create_engine(url=database)
    try:
        yield lambda clock, ids: PostgresStore(engine, clock=clock)
        # nothing may still be holding a connection once the scenario is over
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest.fixture
def store_matrix(flow_store: StoreFactory) -> Iterator[None]:
    """Make every ``make_flow_kit()`` in the test use the parametrized store."""
    with use_store_factory(flow_store):
        yield
