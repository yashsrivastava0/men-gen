from __future__ import annotations

import pytest

from mde_core.clock import FakeClock
from mde_llm_gateway_api.testing import Harness, HarnessFactory, build_harness


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=["inprocess", "http"])
def harness_factory(request: pytest.FixtureRequest, clock: FakeClock) -> HarnessFactory:
    """Builds a harness for the parametrised client kind (every shared test runs for both)."""
    kind: str = request.param

    def make(**kw: object) -> Harness:
        return build_harness(kind, clock, **kw)  # type: ignore[arg-type]

    return make
