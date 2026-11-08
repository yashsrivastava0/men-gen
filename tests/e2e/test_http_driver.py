"""``HttpDriver`` itself: what only the HTTP transport can show.

The journeys in ``test_journeys.py`` already run over it (the ``driver_kind`` matrix). These tests
look at the wire: the committed file as ``GET /spec`` serves it, a stale decision over HTTP, a wrong
token, a pasted secret that must not come back, the event log read back through the stream, and a
whole journey over HTTP on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from evals.harness.driver import HttpDriver, HttpError
from evals.harness.journeys import GUIDED, collect, play_journey, reach_brief_gate
from evals.harness.observe import everything
from evals.harness.stack import NetworkGuard, StackOptions, StoreFactory, build_stack
from mde_contracts.events import Notice, SpecReady

pytestmark = pytest.mark.e2e


def _fake_key() -> str:
    """Key-shaped, assembled at run time so no key-like literal sits in the repository."""
    return "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


async def test_get_spec_serves_the_committed_file_byte_for_byte(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects")
    driver = HttpDriver.for_stack(stack)
    result = await play_journey(driver, GUIDED[0])
    text, headers = await driver.spec_text()
    assert text == result.spec.spec_text
    assert headers["x-spec-hash"] == result.spec.hash
    assert headers["x-spec-version"] == str(result.spec.version)
    assert headers["x-spec-status"] == "handed_off"
    [ready] = result.spec_ready
    assert isinstance(ready, SpecReady) and ready.hash == headers["x-spec-hash"]


async def test_a_stale_decision_over_http_is_a_notice_and_changes_nothing(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects")
    driver = HttpDriver.for_stack(stack)
    await reach_brief_gate(driver, GUIDED[0])
    reply = await driver.decide("brief", "approve", hash="stale")
    assert any(isinstance(e, Notice) and e.code == "stale_gate" for e in reply)
    assert (await driver.snapshot()).state == "BRIEF_REVIEW"
    assert not await driver.approvals()
    await driver.decide("brief", "approve")  # the current hash still works
    assert (await driver.snapshot()).state == "PLANNING"


async def test_a_wrong_token_is_refused_with_the_api_error_body(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    driver = HttpDriver.for_stack(build_stack(tmp_path / "projects"))
    driver.token = "not-the-token"
    with pytest.raises(HttpError) as raised:
        await driver.start()
    assert (raised.value.status, raised.value.code) == (401, "unauthorized")


async def test_a_pasted_secret_is_redacted_and_never_comes_back_over_http(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects")
    driver = HttpDriver.for_stack(stack)
    await driver.start()
    key = _fake_key()
    reply = await driver.say(f"use {key} for payments")
    assert any(isinstance(e, Notice) and e.code == "secret_redacted" for e in reply)
    assert key not in await everything(driver)  # events, records, versions, jobs


async def test_the_log_read_over_the_stream_is_the_log_that_was_sent(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects")
    driver = HttpDriver.for_stack(stack)
    result = await play_journey(driver, GUIDED[0])
    stored = await driver.events()
    assert [e.seq for e in stored] == list(range(1, len(stored) + 1))  # gapless
    assert [e.model_dump_json() for e in stored] == [e.model_dump_json() for e in driver.log]
    assert await driver.fresh() == []  # nothing newer than what was handed out
    assert result.cards_asked == [e.card.card.id for e in stored if e.type == "card"]


@pytest_asyncio.fixture
async def pg_factory(pg_database: object) -> AsyncIterator[StoreFactory]:
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore
    from mde_persistence.testing_plugin import truncate_all

    truncate_all(pg_database)  # type: ignore[arg-type]
    engine = create_engine(url=pg_database)  # type: ignore[arg-type]
    try:
        yield lambda clock, ids: PostgresStore(engine, clock=clock)
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]  # nothing left open
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_a_journey_over_http_on_postgres_ends_in_a_handed_off_spec(
    tmp_path: Path, pg_factory: StoreFactory, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=pg_factory))
    driver = HttpDriver.for_stack(stack)
    result = await play_journey(driver, GUIDED[0])
    assert result.spec.status == "handed_off" and result.spec.git_sha
    text, headers = await driver.spec_text()
    assert text == result.spec.spec_text and headers["x-spec-hash"] == result.spec.hash
    final = await collect(driver, GUIDED[0])
    assert [e.seq for e in final.events] == list(range(1, len(final.events) + 1))
