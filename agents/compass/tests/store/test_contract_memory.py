"""The shared store contract suite against the in-memory fake (no database)."""

from __future__ import annotations

import pytest

from mde_compass.store.contract import CHECKS
from mde_compass.store.memory import InMemoryStore
from mde_compass.store.ports import Store
from mde_core.clock import FakeClock


@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_contract(name: str) -> None:
    store = InMemoryStore(FakeClock())

    async def factory() -> Store:
        return store

    await CHECKS[name](factory)
    assert store.open_units == 0  # nothing left open


def test_contract_registry_is_complete() -> None:
    expected = {
        "session_create_get_list_save",
        "session_save_stale_conflict",
        "turn_exclusive",
        "turn_race_single_winner",
        "events_inbound_idempotent",
        "events_outbound_seq",
        "events_outbound_concurrent",
        "events_history_paging",
        "briefs_consecutive_only",
        "briefs_baseline_and_immutability",
        "tenant_isolation",
        "unit_of_work_rollback",
    }
    assert expected <= set(CHECKS)
