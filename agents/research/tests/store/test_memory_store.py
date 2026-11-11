import pytest

from support.memory_store import InMemoryStore
from support.store_contract import CHECKS


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_in_memory_store_meets_the_contract(check) -> None:
    await check(InMemoryStore())
