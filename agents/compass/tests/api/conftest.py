"""API test fixtures: the real app over a ``ScriptedService``, driven by httpx ``ASGITransport``."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app

HEADERS = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
OTHER_TENANT = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t2", "X-User-Id": "u9"}


@pytest.fixture
def service() -> ScriptedService:
    return ScriptedService()


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings(sse_poll_seconds=0.01, sse_max_poll_seconds=0.02)


@pytest.fixture
async def client(
    service: ScriptedService, settings: ApiSettings
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service, settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=HEADERS) as c:
        yield c


@pytest.fixture
async def session_id(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/v1/compass/sessions", json={"mode": "guided", "locale": "en", "user_label": "Pune food"}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def msg(event_id: str, text: str = "a food delivery app in Pune") -> dict[str, object]:
    return {"type": "user_message", "event_id": event_id, "text": text}
