"""End-to-end fixtures: quiet logs, a socket guard, and the driver matrix (flow and HTTP).

``driver_kind`` runs every conversation twice: over ``FlowDriver`` (the real engines and flow, in
process) and over ``HttpDriver`` (the same stack behind ``apps/context-api``: every message is an
HTTP request on an ASGI transport, so no socket is opened).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import structlog

from evals.harness.driver import Driver, FlowDriver, HttpDriver
from evals.harness.stack import NetworkGuard, Stack, StackOptions, build_stack, network_guard


@pytest.fixture(autouse=True, scope="module")
def _quiet_structlog() -> Iterator[None]:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest.fixture
def socket_guard() -> Iterator[NetworkGuard]:
    """Any attempt to reach a non-loopback host is recorded (and blocked): zero is the only pass."""
    with network_guard() as guard:
        yield guard
    assert guard.attempts == [], f"the run reached the network: {guard.attempts}"


@pytest.fixture(params=["flow", "http"])
def driver_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def open_session(
    tmp_path: Path, driver_kind: str, socket_guard: NetworkGuard
) -> Callable[..., tuple[Stack, Driver]]:
    """``open_session(options=None)`` -> ``(stack, driver)`` over the parametrised transport."""
    counter = 0

    def make(options: StackOptions | None = None) -> tuple[Stack, Driver]:
        nonlocal counter
        counter += 1
        stack = build_stack(tmp_path / f"projects-{counter}", options)
        if driver_kind == "http":
            return stack, HttpDriver.for_stack(stack)
        return stack, FlowDriver.for_user(stack)

    return make
