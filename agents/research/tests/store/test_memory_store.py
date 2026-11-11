import pytest

from mde_research_agent.store import InMemoryStore
from mde_research_agent.store.contract import CHECKS


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_in_memory_store_meets_the_contract(check) -> None:
    await check(InMemoryStore())
