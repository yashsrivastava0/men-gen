"""N4: a tenant (or a token) bound to a set of policies cannot pick another mode or policy.

Without a binding the request decides (development behaviour, covered by ``test_sessions.py``).
With ``TENANT_POLICIES`` or a token entry the request must lie inside it and the defaults come
from it, so a Guided session cannot be used to skip the organisation's rules.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mde_context_api.bindings import PolicyBinding, merge
from mde_context_api.composition import AppState
from mde_context_api.settings import ApiSettings
from mde_context_api.testing import build_state, headers, make_client, make_settings
from mde_core.errors import ConfigError

ENTERPRISE = "enterprise-it-services-firm"
BANK = "regulated-bank"
GUIDED = "guided-default"
PLATFORM = "platform-default"

POLICIES: dict[str, dict[str, Any]] = {
    "locked": {"allowed_policies": [BANK]},  # one enterprise policy, nothing else
    "enterprise-only": {"allowed_policies": [ENTERPRISE, BANK], "default_policy": BANK},
    "guided-pair": {"allowed_policies": [GUIDED, PLATFORM], "default_policy": PLATFORM},
    "mode-only": {"default_mode": "enterprise"},
    "empty": {"allowed_policies": []},
}

Factory = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def make_api(tmp_path: Path) -> AsyncIterator[Factory]:
    """Clients over states built with the given extra settings (all closed afterwards)."""
    states: list[AppState] = []
    clients: list[httpx.AsyncClient] = []

    async def build(**settings: Any) -> httpx.AsyncClient:
        st = await build_state(make_settings(tmp_path / f"p{len(states)}", **settings))
        states.append(st)
        client = make_client(st)
        clients.append(client)
        return client

    yield build
    for client in clients:
        await client.aclose()
    for st in states:
        await st.aclose()


async def create(
    client: httpx.AsyncClient, tenant: str, body: dict[str, Any] | None = None, **hdrs: str
) -> httpx.Response:
    return await client.post("/v1/sessions", json=body or {}, headers={**headers(tenant), **hdrs})


# --------------------------------------------------------------------------- tenant bindings


async def test_a_locked_tenant_cannot_open_a_guided_session(make_api: Factory) -> None:
    client = await make_api(tenant_policies=POLICIES)
    resp = await create(client, "locked", {"mode": "guided"})
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    message = resp.json()["message"]
    assert "guided" in message and "enterprise" in message  # says what is and is not allowed


async def test_a_locked_tenant_cannot_name_another_policy(make_api: Factory) -> None:
    client = await make_api(tenant_policies=POLICIES)
    resp = await create(client, "locked", {"mode": "enterprise", "policy_id": ENTERPRISE})
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    assert BANK in resp.json()["message"]  # names the policy it may use
    # a guided policy by name, with or without a mode: outside the binding, the same refusal
    resp = await create(client, "locked", {"policy_id": GUIDED})
    assert resp.status_code == 403
    resp = await create(client, "locked", {"mode": "guided", "policy_id": GUIDED})
    assert resp.status_code == 403


async def test_a_bound_tenants_defaults_come_from_the_binding(make_api: Factory) -> None:
    client = await make_api(tenant_policies=POLICIES)
    # no mode, no policy: the one allowed policy, in its own mode (not the global Guided default)
    resp = await create(client, "locked")
    assert resp.status_code == 201
    assert (resp.json()["session"]["mode"], resp.json()["session"]["policy_id"]) == (
        "enterprise",
        BANK,
    )
    # an explicit mode that matches, no policy: the binding's default policy
    resp = await create(client, "enterprise-only", {"mode": "enterprise"})
    assert resp.json()["session"]["policy_id"] == BANK
    # a named allowed policy is fine
    resp = await create(client, "enterprise-only", {"policy_id": ENTERPRISE})
    assert resp.status_code == 201 and resp.json()["session"]["policy_id"] == ENTERPRISE
    # a guided-only tenant defaults to ITS default policy, not guided-default
    resp = await create(client, "guided-pair")
    assert (resp.json()["session"]["mode"], resp.json()["session"]["policy_id"]) == (
        "guided",
        PLATFORM,
    )
    # and cannot go Enterprise
    resp = await create(client, "guided-pair", {"mode": "enterprise"})
    assert resp.status_code == 403


async def test_a_default_mode_alone_changes_the_default_and_restricts_nothing(
    make_api: Factory,
) -> None:
    client = await make_api(tenant_policies=POLICIES)
    resp = await create(client, "mode-only")
    assert resp.status_code == 201 and resp.json()["session"]["mode"] == "enterprise"
    assert resp.json()["session"]["policy_id"] == ENTERPRISE  # the catalog default
    resp = await create(client, "mode-only", {"mode": "guided"})
    assert resp.status_code == 201  # an explicit mode is still the caller's choice here


async def test_an_empty_allowed_list_allows_nothing(make_api: Factory) -> None:
    client = await make_api(tenant_policies=POLICIES)
    for body in ({}, {"mode": "guided"}, {"mode": "enterprise"}):
        resp = await create(client, "empty", body)
        assert resp.status_code == 403, body


async def test_a_tenant_without_an_entry_keeps_the_development_behaviour(
    make_api: Factory,
) -> None:
    client = await make_api(tenant_policies=POLICIES)
    for body, policy in (
        ({}, GUIDED),
        ({"mode": "guided"}, GUIDED),
        ({"mode": "enterprise"}, ENTERPRISE),
        ({"mode": "enterprise", "policy_id": BANK}, BANK),
    ):
        resp = await create(client, "free-tenant", body)
        assert resp.status_code == 201 and resp.json()["session"]["policy_id"] == policy
    nothing = await make_api()  # no TENANT_POLICIES at all
    assert (await create(nothing, "locked", {"mode": "guided"})).status_code == 201


async def test_the_org_profile_check_uses_the_bound_mode(make_api: Factory) -> None:
    client = await make_api(tenant_policies=POLICIES)
    ok = await create(client, "locked", {"org_profile": {"brand": {"name": "Acme"}}})
    assert (
        ok.status_code == 201 and ok.json()["org_profile_saved"] is True
    )  # defaulted to enterprise
    bad = await create(client, "guided-pair", {"org_profile": {"brand": {"name": "Acme"}}})
    assert bad.status_code == 422  # a Guided default cannot take an organisation profile


# --------------------------------------------------------------------------- token bindings


TOKEN_A = "alice-token-0123456789"
TOKEN_B = "bob-token-0123456789ab"
TOKEN_C = "carol-token-0123456789"
TOKENS = {
    TOKEN_A: {
        "tenant_id": "acme",
        "user_id": "alice",
        "allowed_policies": [ENTERPRISE, BANK],
        "default_policy": BANK,
    },
    TOKEN_B: {"tenant_id": "acme", "user_id": "bob", "allowed_policies": [GUIDED]},
    TOKEN_C: {"tenant_id": "acme", "user_id": "carol"},
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_token_entry_binds_what_its_holder_may_start(make_api: Factory) -> None:
    client = await make_api(api_tokens=json.dumps(TOKENS))
    alice = await client.post("/v1/sessions", json={}, headers=bearer(TOKEN_A))
    assert alice.status_code == 201
    assert (alice.json()["session"]["mode"], alice.json()["session"]["policy_id"]) == (
        "enterprise",
        BANK,
    )
    denied = await client.post("/v1/sessions", json={"mode": "guided"}, headers=bearer(TOKEN_A))
    assert denied.status_code == 403
    bob = await client.post("/v1/sessions", json={}, headers=bearer(TOKEN_B))
    assert bob.status_code == 201 and bob.json()["session"]["policy_id"] == GUIDED
    assert (
        await client.post("/v1/sessions", json={"mode": "enterprise"}, headers=bearer(TOKEN_B))
    ).status_code == 403
    # a token with no binding is a free caller (development behaviour)
    carol = await client.post("/v1/sessions", json={"mode": "enterprise"}, headers=bearer(TOKEN_C))
    assert carol.status_code == 201 and carol.json()["session"]["policy_id"] == ENTERPRISE


async def test_the_token_and_the_tenant_binding_both_apply(make_api: Factory) -> None:
    client = await make_api(
        api_tokens=json.dumps(TOKENS),
        tenant_policies={"acme": {"allowed_policies": [BANK, GUIDED]}},
    )
    # alice's token allows [enterprise-it-services-firm, regulated-bank], the tenant [bank, guided]:
    # the intersection is the bank policy only
    ok = await client.post("/v1/sessions", json={}, headers=bearer(TOKEN_A))
    assert ok.status_code == 201 and ok.json()["session"]["policy_id"] == BANK
    narrowed = await client.post(
        "/v1/sessions", json={"policy_id": ENTERPRISE}, headers=bearer(TOKEN_A)
    )
    assert narrowed.status_code == 403
    # carol has no token binding, the tenant's still holds
    capped = await client.post(
        "/v1/sessions",
        json={"mode": "enterprise", "policy_id": ENTERPRISE},
        headers=bearer(TOKEN_C),
    )
    assert capped.status_code == 403
    # bob's [guided-default] lies inside the tenant's [bank, guided]
    bob = await client.post("/v1/sessions", json={}, headers=bearer(TOKEN_B))
    assert bob.status_code == 201


async def test_disjoint_token_and_tenant_bindings_start_nothing(make_api: Factory) -> None:
    client = await make_api(
        api_tokens=json.dumps(TOKENS), tenant_policies={"acme": {"allowed_policies": [PLATFORM]}}
    )
    for token in (TOKEN_A, TOKEN_B):
        resp = await client.post("/v1/sessions", json={}, headers=bearer(token))
        assert resp.status_code == 403 and resp.json()["code"] == "forbidden"


# --------------------------------------------------------------------------- configuration


async def test_a_binding_to_an_unknown_policy_stops_the_start_up(tmp_path: Path) -> None:
    bad_tenant = make_settings(
        tmp_path / "a", tenant_policies={"t": {"allowed_policies": ["nope"]}}
    )
    with pytest.raises(ConfigError, match=r"TENANT_POLICIES\[t\]: unknown policy 'nope'"):
        await build_state(bad_tenant)
    bad_token = make_settings(
        tmp_path / "b",
        api_tokens=json.dumps(
            {TOKEN_A: {"tenant_id": "t", "user_id": "u", "allowed_policies": ["nope"]}}
        ),
    )
    with pytest.raises(ConfigError, match=r"API_TOKENS entry 1: unknown policy"):
        await build_state(bad_token)
    mismatch = make_settings(
        tmp_path / "c",
        tenant_policies={"t": {"default_policy": GUIDED, "default_mode": "enterprise"}},
    )
    with pytest.raises(ConfigError, match=r"default_policy is not a enterprise policy"):
        await build_state(mismatch)


@pytest.mark.parametrize(
    "value",
    [
        {"t": {}},  # restricts nothing
        {"t": {"default_policy": BANK, "allowed_policies": [GUIDED]}},  # default not allowed
        {"t": {"allowed_policies": ["Not A Policy"]}},
        {"t": {"default_mode": "both"}},
        {"t": {"allowed_policies": [GUIDED], "extra": 1}},
        {"../x": {"allowed_policies": [GUIDED]}},
        "not json",
    ],
)
def test_a_bad_tenant_policies_setting_is_refused(value: object, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(projects_dir=tmp_path, tenant_policies=value)  # type: ignore[arg-type]


def test_tenant_policies_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TENANT_POLICIES", json.dumps(POLICIES))
    settings = ApiSettings(projects_dir=tmp_path)
    assert settings.tenant_policies["locked"].allowed == frozenset({BANK})
    monkeypatch.setenv("TENANT_POLICIES", "  ")
    assert ApiSettings(projects_dir=tmp_path).tenant_policies == {}


# --------------------------------------------------------------------------- the pure part


def test_merge_intersects_the_allowed_sets_and_keeps_a_default_only_while_allowed() -> None:
    a = PolicyBinding(allowed_policies=(ENTERPRISE, BANK), default_policy=BANK)
    b = PolicyBinding(allowed_policies=(BANK, GUIDED))
    both = merge(a, b)
    assert both is not None and both.allowed == frozenset({BANK}) and both.default_policy == BANK
    assert merge(None, None) is None
    assert merge(PolicyBinding(), None) is None  # restricts nothing
    c = PolicyBinding(allowed_policies=(GUIDED,))
    nothing_shared = merge(a, c)
    assert nothing_shared is not None
    assert nothing_shared.allowed == frozenset() and nothing_shared.default_policy is None
    token_first = merge(
        PolicyBinding(default_mode="guided"), PolicyBinding(default_mode="enterprise")
    )
    assert token_first is not None and token_first.default_mode == "guided"
    only = PolicyBinding(default_policy=BANK)
    assert only.allowed == frozenset({BANK}) and only.restricts
