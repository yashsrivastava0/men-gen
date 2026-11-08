"""The reusable store contract suite, run against ``PostgresStore``."""

from __future__ import annotations

import pytest

from mde_contracts.ports import Store
from mde_persistence.store import PostgresStore
from mde_persistence.testing_contract import CHECKS, StoreFactory

pytestmark = pytest.mark.integration


@pytest.fixture
def make_empty_store(store: PostgresStore) -> StoreFactory:
    async def make() -> Store:
        return store  # the fixture already truncated every table

    return make


@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_contract(name: str, make_empty_store: StoreFactory) -> None:
    await CHECKS[name](make_empty_store)


def test_contract_suite_covers_the_mission() -> None:
    required = {
        "session_save_is_compare_and_set",
        "inbound_events_are_idempotent",
        "outbound_seq_replay_and_replies",
        "spec_versions_and_immutability",
        "one_active_run_per_session",
        "cancel_queued_and_running",
        "tenant_isolation_everywhere",
        "claim_is_exclusive",
    }
    assert required <= set(CHECKS)
