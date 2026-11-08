"""Scenario suite fixtures: quiet logs, a Postgres store factory, and the sibling ``runner`` import.

``--import-mode=importlib`` test modules cannot import siblings, so this conftest puts its own
directory on ``sys.path`` (the same convention as the research and spec-builder test packages).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from evals.harness.stack import StoreFactory  # noqa: E402


@pytest.fixture(autouse=True, scope="module")
def _quiet_structlog() -> Iterator[None]:
    """The engines log one info line per LLM call; keep scenario output readable."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest_asyncio.fixture
async def postgres_store_factory(request: pytest.FixtureRequest) -> AsyncIterator[StoreFactory]:
    """A ``PostgresStore`` factory over the persistence plugin's ephemeral database."""
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore
    from mde_persistence.testing_plugin import truncate_all

    database = request.getfixturevalue("pg_database")
    truncate_all(database)
    engine = create_engine(url=database)
    try:
        yield lambda clock, ids: PostgresStore(engine, clock=clock)
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()
