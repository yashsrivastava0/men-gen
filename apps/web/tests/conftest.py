"""Fixtures for the console smoke tests.

The console itself imports nothing from any agent. These fixtures are the one test-only bridge: they
build the Compass app (``create_app`` over a ``ScriptedService``) so the static files can be fetched
through the real mount, the real CSP middleware and the real API paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app

WEB_ROOT = Path(__file__).resolve().parents[1]  # apps/web

HEADERS = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}


@pytest.fixture(autouse=True)
def _default_console_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the repository's console unless a test sets ``MDE_WEB_DIR`` itself."""
    monkeypatch.delenv("MDE_WEB_DIR", raising=False)


@pytest.fixture
def repo_root() -> Path:
    return WEB_ROOT.parents[1]


@pytest.fixture
def console_dir() -> Path:
    return WEB_ROOT / "console"


@pytest.fixture
def service() -> ScriptedService:
    return ScriptedService()


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings(sse_poll_seconds=0.01, sse_max_poll_seconds=0.02)


@pytest.fixture
def build_app(service: ScriptedService, settings: ApiSettings) -> Callable[[], FastAPI]:
    """Create the Compass app over the scripted service; call it after the environment is set."""

    def build() -> FastAPI:
        # ScriptedService is a test double that does not implement every CompassService method
        return create_app(service, settings=settings)  # pyright: ignore[reportArgumentType]

    return build


@pytest.fixture
async def anon(build_app: Callable[[], FastAPI]) -> AsyncIterator[httpx.AsyncClient]:
    """A client with no auth headers: the console files and the strings endpoint need none."""
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
async def client(build_app: Callable[[], FastAPI]) -> AsyncIterator[httpx.AsyncClient]:
    """A client with the dev auth headers the console sends from its Settings dialog."""
    app = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=HEADERS) as c:
        yield c
