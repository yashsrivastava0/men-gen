"""The same inputs and the same fakes give the same research.json hash, whatever order the web answers in."""

import random

import pytest

from mde_research_agent.core.hashing import content_hash
from mde_research_agent.fakes import FakeWorld
from mde_research_agent.testing import load_fixture, run_engine


@pytest.mark.parametrize(
    "name",
    [
        "quick-yoga-studio-bengaluru",
        "standard-produce-marketplace-nashik",
        "dental-clinic-dubai-ar-en",
    ],
)
async def test_the_same_run_twice_gives_the_same_hash(name: str) -> None:
    a = await run_engine(load_fixture(name))
    b = await run_engine(load_fixture(name))
    assert content_hash(a.research) == content_hash(b.research)
    assert a.research["items"] and a.research["norms"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
async def test_random_delays_never_change_the_result(seed: int) -> None:
    base = await run_engine(load_fixture("standard-produce-marketplace-nashik"))
    rng = random.Random(seed)
    w = FakeWorld(provider_content_every=3, jitter=lambda: rng.random() * 0.004)
    chaos = await run_engine(load_fixture("standard-produce-marketplace-nashik"), world=w)
    assert content_hash(chaos.research) == content_hash(base.research)
