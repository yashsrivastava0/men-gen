"""API tests: the store matrix (in-memory fake / ephemeral PostgreSQL) and an ASGI client.

``state`` yields a fully wired ``AppState`` (real Discover and Research engines on the mock gateway,
the scripted Spec Builder) over each store; ``@pytest.mark.integration`` marks the Postgres variant
(start the local server with ``uv run python scripts/dev_db.py up``; the persistence plugin creates
an ephemeral database, truncated per test, and never touches the dev database ``mde``).

Logging is quiet for the module and structlog's defaults are restored afterwards (D-038): tests must
not leave global state changed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import structlog

from mde_context_api.composition import AppState
from mde_context_api.testing import Api, build_state, make_client, make_settings


@pytest.fixture(autouse=True, scope="module")
def _quiet_structlog() -> Iterator[None]:
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
async def state(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[AppState]:
    settings = make_settings(tmp_path_factory.mktemp("projects"))
    if request.param == "memory":
        st = await build_state(settings)
        try:
            yield st
        finally:
            await st.aclose()
        return
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore
    from mde_persistence.testing_plugin import truncate_all

    database = request.getfixturevalue("pg_database")
    truncate_all(database)
    engine = create_engine(url=database)
    try:
        st = await build_state(settings, PostgresStore(engine))
        try:
            yield st
        finally:
            await st.aclose()
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(state: AppState) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(state) as c:
        yield c


@pytest_asyncio.fixture
async def api(client: httpx.AsyncClient) -> Api:
    return Api(client)


@pytest_asyncio.fixture
async def mem_state(tmp_path: Path) -> AsyncIterator[AppState]:
    """The same wiring over the in-memory store only (for tests that need no store matrix)."""
    st = await build_state(make_settings(tmp_path / "projects"))
    try:
        yield st
    finally:
        await st.aclose()


@pytest_asyncio.fixture
async def mem_client(mem_state: AppState) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(mem_state) as c:
        yield c


@pytest_asyncio.fixture
async def mem_api(mem_client: httpx.AsyncClient) -> Api:
    return Api(mem_client)
