# ruff: noqa: S104
"""Dev auth (one identical 401), CSP and security headers, body limit, bind guard."""

from __future__ import annotations

import httpx
import pytest

from mde_compass.__main__ import bind_refusal, is_loopback, main
from mde_compass.api.middleware import CSP
from mde_compass.api.settings import ApiSettings, load_api_settings, parse_token_table
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app

P = "/v1/compass"
GOOD = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong", "X-Tenant-Id": "t1", "X-User-Id": "u1"},
        {"Authorization": "Basic dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"},
        {"Authorization": "Bearer dev-token", "X-User-Id": "u1"},
        {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1"},
        {"Authorization": "Bearer dev-token", "X-Tenant-Id": "-bad", "X-User-Id": "u1"},
        {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t" * 65, "X-User-Id": "u1"},
        {**GOOD, "X-Roles": "bad role!"},
    ],
)
async def test_every_auth_failure_is_the_same_401(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    r = await client.get(
        f"{P}/sessions", headers=headers
    )  # headers merge over the client's good ones
    plain = await httpx.AsyncClient(transport=client._transport, base_url="http://test").get(
        f"{P}/sessions", headers=headers
    )
    assert plain.status_code == 401
    assert plain.json() == {"code": "unauthorized", "message": "Authentication is required."}
    assert r.status_code in (200, 401)


async def test_roles_header_and_public_paths(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{P}/sessions", headers={**GOOD, "X-Roles": "approver, requester"})
    assert r.status_code == 200
    bare = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    assert (await bare.get("/healthz")).status_code == 200
    assert (await bare.get(f"{P}/strings/en")).status_code == 200
    assert (await bare.get("/ui/")).status_code in (200, 404)  # no auth needed either way
    assert (await bare.get(f"{P}/budget")).status_code == 401


async def test_custom_dev_token_and_token_mode() -> None:
    app = create_app(ScriptedService(), settings=ApiSettings(dev_token="s3cret"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"{P}/sessions", headers=GOOD)).status_code == 401
        ok = {**GOOD, "Authorization": "Bearer s3cret"}
        assert (await c.get(f"{P}/sessions", headers=ok)).status_code == 200
    table = parse_token_table("tokA=acme:alice:approver+requester;tokB=beta:bob")
    app = create_app(ScriptedService(), settings=ApiSettings(token_table=table))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"{P}/sessions", headers=GOOD)).status_code == 401  # dev token refused
        a = {"Authorization": "Bearer tokA", "X-Tenant-Id": "spoofed", "X-User-Id": "spoofed"}
        sid = (await c.post(f"{P}/sessions", headers=a, json={})).json()["id"]
        b = {"Authorization": "Bearer tokB"}
        assert (
            await c.get(f"{P}/sessions/{sid}", headers=b)
        ).status_code == 404  # tenant from the token
        assert (await c.get(f"{P}/budget", headers=a)).status_code == 404  # dev only


def test_token_table_parsing() -> None:
    assert parse_token_table("a=t:u")["a"].roles == ()
    for bad in ("", "a", "a=t", "=t:u", ";"):
        with pytest.raises(ValueError):
            parse_token_table(bad)
    assert load_api_settings({"COMPASS_DEV_TOKEN": "x"}).dev_token == "x"
    assert load_api_settings({}).dev_token == "dev-token"
    assert load_api_settings({"COMPASS_MAX_BODY_BYTES": "1000"}).max_body_bytes == 1000


async def test_security_headers_on_every_response(client: httpx.AsyncClient) -> None:
    unauth = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    paths = ["/healthz", f"{P}/strings/en", f"{P}/sessions", "/nothing", "/ui/", "/ui/app.js", "/"]
    for path in paths:
        r = await unauth.get(path)
        assert r.headers["content-security-policy"] == CSP, path
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["referrer-policy"] == "no-referrer", path
        assert r.headers["x-request-id"], path
    r = await client.get(f"{P}/sessions")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP


async def test_request_id_is_kept_when_well_formed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-Id": "abcd1234efgh"})
    assert r.headers["x-request-id"] == "abcd1234efgh"
    r = await client.get("/healthz", headers={"X-Request-Id": "bad id"})
    assert r.headers["x-request-id"].startswith("req_")


async def test_body_size_limit() -> None:
    app = create_app(ScriptedService(), settings=ApiSettings(max_body_bytes=2048))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=GOOD
    ) as c:
        sid = (await c.post(f"{P}/sessions", json={})).json()["id"]
        big = {"type": "user_message", "event_id": "e1", "text": "x" * 5000}
        r = await c.post(f"{P}/sessions/{sid}/events", json=big)
        assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
        assert r.headers["content-security-policy"] == CSP
        ok = {"type": "user_message", "event_id": "e2", "text": "x" * 500}
        assert (await c.post(f"{P}/sessions/{sid}/events", json=ok)).status_code == 200
        # uploads are excluded from the small-body limit: a body that is not multipart is a 422, never a 413
        r = await c.post(f"{P}/sessions/{sid}/uploads", content=b"y" * 9000)
        assert r.status_code == 422
        # a real multipart upload is read and answered
        r = await c.post(
            f"{P}/sessions/{sid}/uploads",
            files={"file": ("notes.txt", b"Customers must be able to reorder.")},
        )
        assert r.status_code == 200 and r.json()["events"][0]["type"] == "assistant_message"


def test_loopback_detection_and_bind_guard(capsys: pytest.CaptureFixture[str]) -> None:
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("example.com")
    assert bind_refusal("0.0.0.0", ApiSettings()) is not None
    assert bind_refusal("0.0.0.0", ApiSettings(token_table=parse_token_table("a=t:u"))) is None
    assert bind_refusal("127.0.0.1", ApiSettings()) is None
    assert main(["--host", "0.0.0.0", "--port", "8110"]) == 2
    assert "Refusing to listen" in capsys.readouterr().err


async def test_create_app_without_service_composes_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """No service given: nothing is composed at creation; ``compose.build_service`` runs on first use."""
    import sys
    import types

    calls: list[bool] = []
    scripted = ScriptedService()

    async def build_service(memory: bool = False) -> ScriptedService:
        calls.append(memory)
        return scripted

    fake = types.ModuleType("mde_compass.compose")
    fake.build_service = build_service  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mde_compass.compose", fake)
    app = create_app(memory=True)
    assert calls == []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=GOOD
    ) as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 200
        assert calls == []  # health checks never compose
        r = await c.post(f"{P}/sessions", json={})
        assert r.status_code == 201
        assert (await c.get(f"{P}/sessions")).status_code == 200
    assert calls == [True]  # composed once, with the memory switch


async def test_unexpected_error_is_a_500_error_body_without_details() -> None:
    class Broken(ScriptedService):
        async def list_sessions(self, caller):  # type: ignore[no-untyped-def]
            raise RuntimeError("secret detail sk-12345")

    app = create_app(Broken(), settings=ApiSettings())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=GOOD) as c:
        r = await c.get(f"{P}/sessions")
    assert r.status_code == 500 and r.json()["code"] == "internal_error"
    assert "sk-12345" not in r.text
    assert r.headers["content-security-policy"] == CSP


async def test_core_errors_map_to_statuses() -> None:
    from mde_core import errors as e

    class Raising(ScriptedService):
        def __init__(self, exc: Exception) -> None:
            super().__init__()
            self.exc = exc

        async def list_sessions(self, caller):  # type: ignore[no-untyped-def]
            raise self.exc

    cases = [
        (e.NotFound("x"), 404, "not_found"),
        (e.Conflict("x"), 409, "conflict"),
        (e.HashMismatch("x"), 409, "hash_mismatch"),
        (e.GateError("x"), 409, "gate_error"),
        (e.ValidationFailed("x"), 422, "validation_failed"),
        (e.BudgetExceeded("x"), 429, "budget_exceeded"),
        (e.TenantViolation("x"), 404, "tenant_violation"),
    ]
    for exc, status, code in cases:
        app = create_app(Raising(exc), settings=ApiSettings())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=GOOD
        ) as c:
            r = await c.get(f"{P}/sessions")
        assert (r.status_code, r.json()["code"]) == (status, code), exc


async def test_missing_compose_module_is_a_clear_503(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "mde_compass.compose", None)  # makes the import fail
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=GOOD
    ) as c:
        r = await c.get(f"{P}/sessions")
    assert r.status_code == 503 and r.json()["code"] == "not_ready"
