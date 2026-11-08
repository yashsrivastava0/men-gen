"""Whole journeys on the real PostgreSQL store (start it with ``uv run python scripts/dev_db.py up``).

The same product, the same scripted conversations as the e2e suite, with ``PostgresStore`` instead
of the in-memory fake: the spec reaches ``handed_off`` with its approvals on one hash, the event log
has consecutive sequence numbers, exactly one ``spec_ready`` row exists, and what the hand-off wrote
to disk matches the row. Tests use an ephemeral database (never ``mde``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from evals.harness.checks import check_spec, read_handoff_repo
from evals.harness.driver import FlowDriver
from evals.harness.journeys import ENTERPRISE, GUIDED, play_journey
from evals.harness.observe import dump_postgres
from evals.harness.stack import NetworkGuard, StackOptions, StoreFactory, build_stack, network_guard

pytestmark = pytest.mark.integration


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


@pytest.fixture
def no_network() -> Iterator[NetworkGuard]:
    with network_guard() as guard:
        yield guard
    assert guard.attempts == []


@pytest.mark.parametrize("journey", [GUIDED[0], ENTERPRISE[0]], ids=lambda j: j.name)
async def test_a_journey_on_postgres_ends_in_a_valid_hand_off(
    journey,
    tmp_path: Path,
    pg_factory: StoreFactory,
    pg_dsn: str,
    no_network,  # type: ignore[no-untyped-def]
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=pg_factory))
    driver = FlowDriver.for_user(stack)
    result = await play_journey(driver, journey)
    assert journey.mode == (await driver.session_record()).mode
    policy_id = journey.policy_id or "guided-default"
    checks = check_spec(result.spec, stack.policies(policy_id))
    assert checks.ok, checks.problems
    assert result.spec.status == "handed_off" and result.spec.git_sha
    [ready] = result.spec_ready
    assert ready.hash == result.spec.hash
    repo = read_handoff_repo(stack.repo, result.tenant_id, result.app_id)
    assert repo.spec_text == result.spec.spec_text and repo.commits == 1

    # the outbound log is gapless and holds one spec_ready; approvals are on the final hashes
    events = await driver.events()
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    plan_approvals = [a for a in result.approvals if a.gate == "plan"]
    assert plan_approvals and {(a.version, a.hash) for a in plan_approvals} == {
        (result.spec.version, result.spec.hash)
    }
    # the raw rows agree: one spec row per version, one handed_off, the same hash
    tables = dump_postgres(pg_dsn)
    assert result.spec.hash in tables["spec_version"]
    assert tables["spec_version"].count('"status":"handed_off"') == 1
    assert tables["session_event:out"].count('"type":"spec_ready"') == 1


async def test_two_sessions_of_two_tenants_share_a_database_without_seeing_each_other(
    tmp_path: Path,
    pg_factory: StoreFactory,
    no_network,  # type: ignore[no-untyped-def]
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=pg_factory))
    a = FlowDriver.for_user(stack, user_id="user-1", tenant_id="tenant-a")
    b = FlowDriver.for_user(stack, user_id="user-1", tenant_id="tenant-b")
    await a.start()
    await b.start()
    await a.say("An online shop for handmade candles")
    await b.say("A booking app for my hair salon so clients can book appointments")
    rec_a, rec_b = await a.session_record(), await b.session_record()
    assert rec_a.id != rec_b.id and rec_a.tenant_id != rec_b.tenant_id
    assert rec_a.discovery.slots["idea.archetype"].value == "commerce"
    assert rec_b.discovery.slots["idea.archetype"].value == "booking_services"
    # each tenant's event log holds only its own conversation
    texts_a = " ".join(e.model_dump_json() for e in await a.events())
    texts_b = " ".join(e.model_dump_json() for e in await b.events())
    assert rec_a.id in texts_a and rec_b.id not in texts_a
    assert rec_b.id in texts_b and rec_a.id not in texts_b
