"""Fixtures of the CLI tests.

* ``runner``: a Typer ``CliRunner`` (stdout and stderr kept apart).
* ``api``: an ``ApiClient`` on an ASGI transport over the REAL Discover and Research engines on the
  mock gateway, the scripted Spec Builder (fast) and the in-memory store, with the job worker
  running: the whole HTTP API without a database. ``ApiClient.buffered`` is on, as for ``--local``.
* ``JOURNEY``: the script of a Guided conversation from "hi" to ``spec_ready`` (the real stack, so
  the tests that play it use ``--local --memory``).

Logging is quiet for the module and structlog's defaults are restored afterwards (D-038).
"""

from __future__ import annotations

import logging
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import structlog
from typer.testing import CliRunner

from mde_cli.client import ApiClient, Identity
from mde_context_api.composition import AppState
from mde_context_api.main import create_app
from mde_context_api.testing import TEST_TOKEN, build_state, make_settings

JOURNEY: list[str] = [
    "hi",
    "An online shop for handmade candles",
    "r*",  # the suggested answer to every question
    "1",  # Gate 1: Looks right
    "1",  # Gate 2: Looks right
]


@pytest.fixture(autouse=True, scope="module")
def _quiet_structlog() -> Iterator[None]:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def _no_outside_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Offline means offline: a connection to anything but this machine fails the test."""
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> None:
        host = str(address[0]) if isinstance(address, tuple) and address else str(address)
        if host not in ("127.0.0.1", "::1", "localhost") and not host.startswith("127."):
            attempts.append(host)
            raise OSError(f"offline test tried to reach {host!r}")
        real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    yield
    assert attempts == [], f"the test reached the network: {attempts}"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def journey() -> list[str]:
    """A fresh copy of ``JOURNEY`` (test modules cannot import this one: D-017)."""
    return list(JOURNEY)


@pytest.fixture
def write_script(tmp_path: Path) -> Callable[[list[str]], str]:
    """``write_script(lines)`` -> the path of a UTF-8 script file."""

    def make(lines: list[str], name: str = "script.txt") -> str:
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    return make


@pytest_asyncio.fixture
async def state(tmp_path: Path) -> AsyncIterator[AppState]:
    st = await build_state(make_settings(tmp_path / "projects"))
    st.start_worker()
    try:
        yield st
    finally:
        await st.aclose()


@pytest_asyncio.fixture
async def api(state: AppState) -> AsyncIterator[ApiClient]:
    app = create_app(state=state)
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )
    client = ApiClient(http, TEST_TOKEN, Identity("tenant-a", "user-1"), buffered=True, poll_s=0.02)
    try:
        yield client
    finally:
        await client.aclose()
