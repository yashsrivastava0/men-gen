"""The persistence store contract suite, run against the flow package's ``InMemoryStore``.

``PostgresStore`` passes the same ``CHECKS`` (packages/persistence/tests/test_store_contract.py,
integration). If this file passes too, the fake that every flow unit and property test is built on
behaves like production, so those tests prove something about the real system. Offline: no database.
"""

from __future__ import annotations

import pytest

from mde_contracts.ports import Store
from mde_flow.testing import InMemoryStore
from mde_persistence.testing_contract import CHECKS


@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_in_memory_store_honours_the_store_contract(name: str) -> None:
    async def make() -> Store:
        return InMemoryStore()

    await CHECKS[name](make)
