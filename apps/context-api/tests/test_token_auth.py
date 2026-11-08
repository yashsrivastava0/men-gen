"""N3: dev headers are trusted identity; ``API_TOKENS`` is the server-side identity mode.

Dev mode (the default): one shared token and self-declared ``X-User-Id`` / ``X-Roles``, so its
holder can be two approvers (documented, local use only). Token mode: the token selects the
identity on the server and the headers are ignored, so a forged header changes nothing. The
programmatic entry point refuses to put dev mode on a network.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr, ValidationError

from mde_context_api import __main__ as entry
from mde_context_api.auth import authenticate, authenticate_caller
from mde_context_api.composition import AppState
from mde_context_api.errors import Unauthorized
from mde_context_api.identity import parse_api_tokens
from mde_context_api.settings import ApiSettings
from mde_context_api.testing import (
    TEST_TOKEN,
    Api,
    build_state,
    headers,
    make_client,
    make_settings,
)
from mde_contracts.events import SpecReady

ALICE = "alice-token-0123456789"
BOB = "bob-token-0123456789ab"
CAROL = "carol-token-0123456789"
OTHER_TENANT = "other-token-0123456789"
TOKENS: dict[str, dict[str, Any]] = {
    ALICE: {"tenant_id": "acme", "user_id": "alice"},
    BOB: {"tenant_id": "acme", "user_id": "bob", "roles": ["client_product_owner"]},
    CAROL: {"tenant_id": "acme", "user_id": "carol", "roles": ["delivery_lead"]},
    OTHER_TENANT: {"tenant_id": "globex", "user_id": "alice"},
}
UNAUTHORIZED = {"code": "unauthorized", "message": "Missing or invalid credentials."}


def bearer(token: str, **forged: str) -> dict[str, str]:
    """A token plus whatever X-* headers a caller cares to write."""
    return {"Authorization": f"Bearer {token}", **forged}


@pytest_asyncio.fixture
async def token_state(tmp_path: Path) -> AsyncIterator[AppState]:
    st = await build_state(make_settings(tmp_path / "projects", api_tokens=json.dumps(TOKENS)))
    try:
        yield st
    finally:
        await st.aclose()


@pytest_asyncio.fixture
async def token_client(token_state: AppState) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(token_state) as c:
        yield c


def as_token(client: httpx.AsyncClient, token: str, session_id: str = "", **forged: str) -> Api:
    """The ``Api`` driver speaking as one configured token (it adds no identity headers itself)."""
    api = Api(client)
    api.headers = bearer(token, **forged)
    api.session_id = session_id
    return api


# --------------------------------------------------------------------------- the sybil attack


async def run_to_gate2(owner: Api, state: AppState, mode: str = "enterprise") -> Any:
    await owner.create(mode)
    await owner.to_gate1()
    await owner.approve("brief")
    await state.flow.run_next_job()
    await owner.refresh()
    gate = owner.last_gate("plan")
    assert gate is not None and gate.required_roles == ["client_product_owner", "delivery_lead"]
    return gate


async def test_in_dev_mode_one_token_can_invent_both_approvers(
    mem_state: AppState, mem_client: httpx.AsyncClient
) -> None:
    """Documents the dev-header trust (the reason it must never face a network): with the single
    shared token a caller writes X-User-Id and X-Roles and so approves as two people."""
    owner = Api(mem_client, user="alice")
    await run_to_gate2(owner, mem_state)
    po = owner.as_user("x1-invented", roles=["client_product_owner"])
    lead = owner.as_user("x2-invented", roles=["delivery_lead"])
    assert (await po.decide("plan", role="client_product_owner")).status_code == 200
    done = await lead.decide("plan", role="delivery_lead")
    assert any(e["type"] == "spec_ready" for e in done.json()["events"])


async def test_with_api_tokens_forged_headers_cannot_mint_approvers(
    token_state: AppState, token_client: httpx.AsyncClient
) -> None:
    """The same attack in token mode: the owner's token plus invented headers is still the owner,
    and the owner holds neither approver role, so Gate 2 stays open."""
    owner = as_token(token_client, ALICE)
    gate = await run_to_gate2(owner, token_state)
    for invented, role in (
        ("x1-invented", "client_product_owner"),
        ("x2-invented", "delivery_lead"),
    ):
        forged = as_token(
            token_client,
            ALICE,
            owner.session_id,
            **{"X-User-Id": invented, "X-Roles": role, "X-Tenant-Id": "acme"},
        )
        resp = await forged.post(
            {
                "type": "gate_decision",
                "event_id": f"evt-forged-{invented}",
                "gate": "plan",
                "decision": "approve",
                "version": gate.version,
                "hash": gate.hash,
                "role": role,
            }
        )
        assert resp.status_code == 409 and resp.json()["code"] == "gate_error"
    assert (await owner.snapshot())["state"] == "PLAN_REVIEW"
    assert not any(isinstance(e, SpecReady) for e in await owner.refresh())


async def test_with_api_tokens_the_real_approvers_complete_the_gate(
    token_state: AppState, token_client: httpx.AsyncClient
) -> None:
    owner = as_token(token_client, ALICE)
    await run_to_gate2(owner, token_state)
    bob = as_token(token_client, BOB, owner.session_id)
    carol = as_token(token_client, CAROL, owner.session_id)
    bob.events, carol.events = list(owner.events), list(owner.events)
    first = await bob.decide("plan")
    assert first.status_code == 200 and first.json()["state"] == "PLAN_REVIEW"
    done = await carol.decide("plan")
    assert done.json()["state"] == "HANDED_OFF"
    assert any(e["type"] == "spec_ready" for e in done.json()["events"])


@pytest.mark.parametrize(
    "forged",
    [
        {"X-User-Id": "mallory"},
        {"X-Tenant-Id": "globex"},
        {"X-Roles": "requester,delivery_lead,client_product_owner"},
        {"X-Tenant-Id": "globex", "X-User-Id": "alice", "X-Roles": "delivery_lead"},
        {"X-Tenant-Id": "../escape", "X-User-Id": "bad user", "X-Roles": "not fine!"},
    ],
    ids=["user", "tenant", "roles", "everything", "malformed"],
)
async def test_a_forged_header_changes_nothing_with_api_tokens(
    token_client: httpx.AsyncClient, forged: dict[str, str]
) -> None:
    """Whatever the headers say (even garbage that would be a 401 in dev mode), the token alone
    decides who the caller is: the session is alice's, in acme, with no roles."""
    created = await token_client.post("/v1/sessions", json={}, headers=bearer(ALICE, **forged))
    assert created.status_code == 201
    sid = created.json()["session"]["session_id"]
    # the owner is alice of acme: reading with her bare token works ...
    assert (await token_client.get(f"/v1/sessions/{sid}", headers=bearer(ALICE))).status_code == 200
    # ... and a different token of another tenant (also called alice) cannot see it
    other = await token_client.get(f"/v1/sessions/{sid}", headers=bearer(OTHER_TENANT))
    assert other.status_code == 404
    # bob (same tenant, a role that no gate of this session requires) cannot see it either
    assert (await token_client.get(f"/v1/sessions/{sid}", headers=bearer(BOB))).status_code == 404


async def test_headers_are_not_required_in_token_mode(token_client: httpx.AsyncClient) -> None:
    resp = await token_client.post("/v1/sessions", json={}, headers=bearer(ALICE))
    assert resp.status_code == 201


@pytest.mark.parametrize(
    "hdrs",
    [
        {},
        bearer(TEST_TOKEN, **{"X-Tenant-Id": "acme", "X-User-Id": "alice"}),  # the dev token
        bearer("dev-token", **{"X-Tenant-Id": "acme", "X-User-Id": "alice"}),
        bearer("unknown-token-0123456789"),
        bearer(ALICE[:-1]),
        bearer(ALICE + "x"),
        bearer(""),
        {"Authorization": f"Basic {ALICE}"},
        {"Authorization": ALICE},
    ],
    ids=[
        "none",
        "dev-token-from-settings",
        "dev-token-default",
        "unknown",
        "prefix",
        "extended",
        "empty",
        "basic",
        "no-scheme",
    ],
)
async def test_an_unknown_token_is_the_same_401_in_token_mode(
    token_client: httpx.AsyncClient, hdrs: dict[str, str]
) -> None:
    resp = await token_client.post("/v1/sessions", json={}, headers=hdrs)
    assert resp.status_code == 401 and resp.json() == UNAUTHORIZED
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_the_budget_route_needs_a_configured_token_too(
    token_client: httpx.AsyncClient,
) -> None:
    assert (await token_client.get("/v1/budget")).status_code == 401
    assert (await token_client.get("/v1/budget", headers=bearer(ALICE))).status_code == 200


# --------------------------------------------------------------------------- the token table


def test_every_configured_token_is_compared_in_constant_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, api_tokens=json.dumps(TOKENS))
    seen: list[int] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append(len(a) + len(b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    caller = authenticate_caller(ALICE, None, None, None, settings)
    assert (caller.principal.tenant_id, caller.principal.user_id) == ("acme", "alice")
    first = len(seen)
    with pytest.raises(Unauthorized):
        authenticate_caller("not-a-configured-token", None, None, None, settings)
    with pytest.raises(Unauthorized):
        authenticate_caller(None, None, None, None, settings)
    # every call compared the presented token against all four, fixed-length (a match, an early
    # hit and a miss all cost the same), and nothing compared raw token text
    assert first == len(TOKENS) and len(seen) == 3 * len(TOKENS)
    assert set(seen) == {64}


def test_the_headers_are_ignored_by_authenticate_in_token_mode(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, api_tokens=json.dumps(TOKENS))
    who = authenticate(BOB, "globex", "mallory", "delivery_lead,requester", settings)
    assert (who.tenant_id, who.user_id, who.roles) == ("acme", "bob", ["client_product_owner"])


def test_api_tokens_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("API_TOKENS", json.dumps(TOKENS))
    settings = ApiSettings(projects_dir=tmp_path)
    assert settings.token_mode and len(settings.token_table or ()) == len(TOKENS)
    assert ALICE not in repr(settings) and ALICE not in str(settings.model_dump())
    # configured tokens are scrubbed from logs like every other secret
    assert set(TOKENS) <= set(settings.secret_values())
    monkeypatch.setenv("API_TOKENS", "   ")
    assert not ApiSettings(projects_dir=tmp_path).token_mode  # blank means "not configured"
    monkeypatch.delenv("API_TOKENS")
    assert not ApiSettings(projects_dir=tmp_path).token_mode


SECRET_BIT = "SENTINEL-secret-value-9"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        "[1, 2]",
        json.dumps({"short": {"tenant_id": "t", "user_id": "u"}}),
        json.dumps({"dev-token": {"tenant_id": "t", "user_id": "u"}}),
        json.dumps({f"has space-{SECRET_BIT}": {"tenant_id": "t", "user_id": "u"}}),
        json.dumps({f"ok-token-{SECRET_BIT}": "not-an-object"}),
        json.dumps({f"ok-token-{SECRET_BIT}": {"user_id": "u"}}),
        json.dumps({f"ok-token-{SECRET_BIT}": {"tenant_id": "../x", "user_id": "u"}}),
        json.dumps({f"ok-token-{SECRET_BIT}": {"tenant_id": "t", "user_id": "bad user"}}),
        json.dumps(
            {f"ok-token-{SECRET_BIT}": {"tenant_id": "t", "user_id": "u", "roles": ["no no"]}}
        ),
        json.dumps(
            {f"ok-token-{SECRET_BIT}": {"tenant_id": "t", "user_id": "u", "roles": ["r"] * 21}}
        ),
        json.dumps({f"ok-token-{SECRET_BIT}": {"tenant_id": "t", "user_id": "u", "admin": True}}),
        json.dumps(
            {
                f"ok-token-{SECRET_BIT}": {
                    "tenant_id": "t",
                    "user_id": "u",
                    "allowed_policies": ["a"],
                    "default_policy": "b",
                }
            }
        ),
    ],
)
def test_a_bad_token_table_is_refused_without_echoing_a_value(raw: str) -> None:
    with pytest.raises(ValueError, match=r"API_TOKENS") as err:
        parse_api_tokens(raw, forbidden=("dev-token",))
    assert SECRET_BIT not in str(err.value) and "bad user" not in str(err.value)


def test_a_bad_token_table_stops_settings_from_loading(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as err:
        ApiSettings(
            projects_dir=tmp_path,
            api_tokens=SecretStr(json.dumps({f"ok-token-{SECRET_BIT}": {"user_id": "u"}})),
        )
    assert SECRET_BIT not in str(err.value) and "API_TOKENS entry 1" in str(err.value)


def test_a_token_entry_carries_roles_and_a_policy_binding() -> None:
    table = parse_api_tokens(
        json.dumps(
            {
                ALICE: {
                    "tenant_id": "acme",
                    "user_id": "alice",
                    "roles": ["a", "b"],
                    "allowed_policies": ["regulated-bank"],
                    "default_mode": "enterprise",
                }
            }
        )
    )
    [(_, entry_)] = table.entries
    assert entry_.principal().roles == ["a", "b"]
    binding = entry_.binding()
    assert binding is not None and binding.allowed == frozenset({"regulated-bank"})
    plain = parse_api_tokens(json.dumps({BOB: {"tenant_id": "acme", "user_id": "bob"}}))
    assert plain.entries[0][1].binding() is None


# --------------------------------------------------------------------------- the entry point


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.5.4.3", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),  # noqa: S104
        ("::", False),
        ("192.168.1.20", False),
        ("10.0.0.5", False),
        ("api.example.com", False),
        ("", False),
    ],
)
def test_only_loopback_addresses_count_as_loopback(host: str, loopback: bool) -> None:
    assert entry.is_loopback(host) is loopback


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``python -m mde_context_api`` with uvicorn replaced by a recorder."""
    import uvicorn

    captured: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    monkeypatch.setattr(entry, "configure_logging", lambda *a, **k: None)
    for name in ("API_TOKENS", "API_HOST", "API_DEV_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return captured


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "api.example.com"])  # noqa: S104
def test_dev_authentication_is_refused_on_a_network_address(
    served: dict[str, Any], host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--host", host]) == 2
    assert served == {}  # nothing was started
    err = capsys.readouterr().err
    assert host in err and "API_TOKENS" in err and "dev authentication" in err


def test_the_refusal_also_covers_a_network_api_host_from_the_environment(
    served: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_HOST", "0.0.0.0")  # noqa: S104
    assert entry.main([]) == 2 and served == {}


def test_a_customised_dev_token_does_not_make_dev_headers_safe(
    served: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The risk is the self-declared identity, not only the guessable default token."""
    monkeypatch.setenv("API_DEV_TOKEN", "a-long-random-looking-token-0123456789")
    assert entry.main(["--host", "0.0.0.0"]) == 2  # noqa: S104
    assert served == {}


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_dev_authentication_still_serves_loopback(served: dict[str, Any], host: str) -> None:
    assert entry.main(["--host", host]) == 0
    assert served["host"] == host


def test_a_network_address_is_allowed_with_api_tokens(
    served: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKENS", json.dumps(TOKENS))
    assert entry.main(["--host", "0.0.0.0"]) == 0  # noqa: S104
    assert served["host"] == "0.0.0.0"  # noqa: S104


def test_a_bad_token_table_exits_with_a_message_that_has_no_value(
    served: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("API_TOKENS", json.dumps({f"ok-token-{SECRET_BIT}": {"user_id": "u"}}))
    assert entry.main([]) == 2 and served == {}
    err = capsys.readouterr().err
    assert "API_TOKENS entry 1" in err and SECRET_BIT not in err


def test_dev_headers_still_work_exactly_as_before(tmp_path: Path) -> None:
    """Dev mode is unchanged: no API_TOKENS means the shared token plus headers."""
    settings = make_settings(tmp_path)
    assert not settings.token_mode
    who = authenticate(TEST_TOKEN, "t1", "u1", "delivery_lead", settings)
    assert (who.tenant_id, who.user_id, who.roles) == ("t1", "u1", ["delivery_lead"])
    with pytest.raises(Unauthorized):
        authenticate(ALICE, "t1", "u1", None, settings)  # a table token means nothing here
    assert headers()["Authorization"] == f"Bearer {TEST_TOKEN}"
