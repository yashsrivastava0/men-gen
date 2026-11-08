"""Authentication: a bearer token plus identity headers; anything else is the same 401."""

from __future__ import annotations

import hmac

import httpx
import pytest

from mde_context_api.auth import authenticate
from mde_context_api.errors import Unauthorized
from mde_context_api.testing import TEST_TOKEN, headers, make_settings

PROTECTED = [
    ("POST", "/v1/sessions"),
    ("GET", "/v1/sessions/ses_x"),
    ("POST", "/v1/sessions/ses_x/events"),
    ("GET", "/v1/sessions/ses_x/stream"),
    ("GET", "/v1/sessions/ses_x/brief"),
    ("GET", "/v1/sessions/ses_x/plan"),
    ("GET", "/v1/sessions/ses_x/spec"),
    ("GET", "/v1/sessions/ses_x/versions"),
    ("GET", "/v1/budget"),
]


async def _call(client: httpx.AsyncClient, method: str, path: str, hdrs: dict[str, str]):
    body = {"mode": "guided"} if (method, path) == ("POST", "/v1/sessions") else None
    if method == "POST" and body is None:
        body = {"type": "user_message", "event_id": "evt-1", "text": "hi"}
    return await client.request(method, path, headers=hdrs, json=body)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_every_protected_route_refuses_a_request_with_no_credentials(
    mem_client: httpx.AsyncClient, method: str, path: str
) -> None:
    resp = await _call(mem_client, method, path, {})
    assert resp.status_code == 401
    assert resp.json() == {"code": "unauthorized", "message": "Missing or invalid credentials."}
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "hdrs",
    [
        headers(token="wrong-token"),
        headers(token=""),
        {**headers(token=None), "Authorization": f"Basic {TEST_TOKEN}"},
        {**headers(token=None), "Authorization": TEST_TOKEN},
        {k: v for k, v in headers().items() if k != "X-Tenant-Id"},
        {k: v for k, v in headers().items() if k != "X-User-Id"},
        headers(tenant=""),
        headers(tenant="../escape"),
        headers(tenant="a b"),
        headers(tenant="x" * 65),
        headers(user="bad user"),
        headers(roles=["fine", "not fine!"]),
        headers(roles=[f"r{i}" for i in range(21)]),
    ],
    ids=[
        "wrong-token",
        "empty-token",
        "basic-scheme",
        "no-scheme",
        "no-tenant",
        "no-user",
        "empty-tenant",
        "path-tenant",
        "space-tenant",
        "long-tenant",
        "bad-user",
        "bad-role",
        "too-many-roles",
    ],
)
async def test_bad_credentials_all_look_the_same(
    mem_client: httpx.AsyncClient, hdrs: dict[str, str]
) -> None:
    resp = await mem_client.post("/v1/sessions", json={"mode": "guided"}, headers=hdrs)
    assert resp.status_code == 401
    assert resp.json() == {"code": "unauthorized", "message": "Missing or invalid credentials."}


async def test_valid_credentials_are_accepted_and_roles_are_optional(
    mem_client: httpx.AsyncClient,
) -> None:
    ok = await mem_client.post("/v1/sessions", json={}, headers=headers())
    assert ok.status_code == 201
    with_roles = await mem_client.post(
        "/v1/sessions", json={}, headers=headers(roles=["requester", "delivery_lead"])
    )
    assert with_roles.status_code == 201


async def test_health_endpoints_need_no_credentials(mem_client: httpx.AsyncClient) -> None:
    assert (await mem_client.get("/healthz")).status_code == 200
    assert (await mem_client.get("/readyz")).status_code == 200


def test_the_principal_comes_from_the_headers(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings = make_settings(tmp_path_factory.mktemp("p"))
    who = authenticate(
        TEST_TOKEN,
        " tenant-9 ",
        "alice@example.com",
        "delivery_lead, ,client_product_owner,delivery_lead",
        settings,
    )
    assert who.tenant_id == "tenant-9"
    assert who.user_id == "alice@example.com"
    assert who.roles == [
        "delivery_lead",
        "client_product_owner",
    ]  # trimmed, de-duplicated, in order
    with pytest.raises(Unauthorized):
        authenticate(None, "tenant-9", "alice", None, settings)


def test_the_token_is_compared_in_constant_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    settings = make_settings(tmp_path_factory.mktemp("p"))
    seen: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    authenticate(TEST_TOKEN, "t1", "u1", None, settings)
    with pytest.raises(Unauthorized):
        authenticate("nope", "t1", "u1", None, settings)
    with pytest.raises(Unauthorized):
        authenticate(None, "t1", "u1", None, settings)  # a missing token is compared too
    assert [a for a, _ in seen] == [TEST_TOKEN.encode(), b"nope", b""]
    assert all(b == TEST_TOKEN.encode() for _, b in seen)


async def test_a_non_ascii_token_does_not_crash_the_comparison(
    mem_client: httpx.AsyncClient,
) -> None:
    raw = [
        (b"authorization", "Bearer töken".encode("latin-1")),
        (b"x-tenant-id", b"t1"),
        (b"x-user-id", b"u1"),
    ]
    resp = await mem_client.post("/v1/sessions", json={}, headers=raw)
    assert resp.status_code == 401
