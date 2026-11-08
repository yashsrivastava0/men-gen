"""Creating and reading sessions: greeting events, policy choice, org profile, 404s."""

from __future__ import annotations

import httpx
import pytest

from mde_context_api.composition import AppState
from mde_context_api.testing import Api, headers
from mde_contracts.events import AssistantMessage, CardEvent


async def test_create_a_guided_session_returns_the_greeting(api: Api) -> None:
    data = await api.create()
    session = data["session"]
    assert session["state"] == "DISCOVERING"
    assert session["mode"] == "guided" and session["policy_id"] == "guided-default"
    assert session["session_id"].startswith("ses_") and session["app_id"].startswith("app_")
    assert session["pending_card"] is None and session["gate"] is None
    assert session["last_seq"] == 1 and session["active_job"] is None
    assert data["org_profile_saved"] is False

    [greeting] = api.events
    assert isinstance(greeting, AssistantMessage)
    assert greeting.canned and len(greeting.chips) >= 3  # starter chips, no LLM
    assert greeting.seq == 1 and greeting.session_id == session["session_id"]
    assert not any(isinstance(e, CardEvent) for e in api.events)


async def test_the_greeting_is_in_the_event_log_for_replay(api: Api) -> None:
    await api.create()
    stored = await api.refresh()
    assert [e.seq for e in stored] == [1] and stored[0].type == "assistant_message"


async def test_the_mode_defaults_to_guided_and_an_empty_body_works(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post("/v1/sessions", json={}, headers=headers())
    assert resp.status_code == 201
    assert resp.json()["session"]["mode"] == "guided"


async def test_enterprise_uses_the_configurable_default_policy(api: Api, state: AppState) -> None:
    data = await api.create("enterprise")
    assert data["session"]["policy_id"] == state.settings.default_enterprise_policy
    assert data["session"]["policy_id"] == "enterprise-it-services-firm"
    assert data["session"]["mode"] == "enterprise"


async def test_a_policy_id_overrides_the_default_when_it_matches_the_mode(api: Api) -> None:
    data = await api.create("enterprise", policy_id="regulated-bank")
    assert data["session"]["policy_id"] == "regulated-bank"


async def test_an_unknown_policy_is_refused(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/sessions", json={"mode": "guided", "policy_id": "no-such-policy"}, headers=headers()
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    assert "no-such-policy" in resp.json()["message"]


@pytest.mark.parametrize(
    ("mode", "policy_id"),
    [("guided", "enterprise-it-services-firm"), ("enterprise", "guided-default")],
)
async def test_a_policy_of_the_wrong_mode_is_refused(
    client: httpx.AsyncClient, mode: str, policy_id: str
) -> None:
    resp = await client.post(
        "/v1/sessions", json={"mode": mode, "policy_id": policy_id}, headers=headers()
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "teenage"},
        {"policy_id": "Has Spaces"},
        {"policy_id": ""},
        {"surprise": True},
        {"org_profile": "not an object"},
    ],
)
async def test_malformed_create_requests_are_422(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    resp = await client.post("/v1/sessions", json=body, headers=headers())
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


async def test_an_org_profile_is_stored_for_the_tenant(api: Api, state: AppState) -> None:
    profile = {"brand": {"name": "Acme", "color": "#112233"}, "approvers": ["a", "b"]}
    data = await api.create("enterprise", org_profile=profile)
    assert data["org_profile_saved"] is True
    assert await state.org_profiles.get("tenant-a") == profile
    assert await state.org_profiles.get("tenant-b") is None  # tenant-bound


async def test_an_org_profile_is_refused_in_a_guided_session(
    api: Api, client: httpx.AsyncClient, state: AppState
) -> None:
    resp = await client.post(
        "/v1/sessions", json={"mode": "guided", "org_profile": {"x": 1}}, headers=headers()
    )
    assert resp.status_code == 422
    assert "Enterprise" in resp.json()["message"]
    assert await state.org_profiles.get("tenant-a") is None


async def test_an_oversized_org_profile_is_refused_and_no_session_is_made(
    client: httpx.AsyncClient, state: AppState
) -> None:
    big = {"notes": "x" * (state.settings.max_org_profile_bytes + 10)}
    resp = await client.post(
        "/v1/sessions", json={"mode": "enterprise", "org_profile": big}, headers=headers()
    )
    assert resp.status_code == 422
    assert "too large" in resp.json()["message"]
    async with state.store.begin("tenant-a") as uow:
        assert await uow.sessions.list_for_owner("user-1") == []


async def test_get_a_session_reports_where_the_user_left_off(api: Api) -> None:
    await api.create()
    await api.to_gate1()
    snap = await api.snapshot()
    assert snap["state"] == "BRIEF_REVIEW"
    assert snap["gate"]["gate"] == "brief" and snap["gate"]["status"]["status"] == "open"
    assert snap["gate"]["buttons"] == ["Looks right", "Change something"]
    assert snap["brief_version"] == 1 and snap["approved_brief_version"] is None
    assert snap["slots"]["idea.archetype"]["value"]
    assert snap["last_seq"] == max(e.seq for e in await api.refresh())


async def test_a_session_waiting_on_a_card_shows_it(api: Api) -> None:
    await api.create()
    greeting = api.events[0]
    assert isinstance(greeting, AssistantMessage)
    await api.say(greeting.chips[0])
    snap = await api.snapshot()
    assert snap["state"] == "DISCOVERING"
    assert snap["pending_card"]["card"]["id"]
    assert snap["pending_card"]["instance_id"].startswith("crd_")


async def test_an_unknown_session_is_a_404(client: httpx.AsyncClient) -> None:
    for tail in ("", "/brief", "/plan", "/spec", "/versions"):
        resp = await client.get(f"/v1/sessions/ses_nope{tail}", headers=headers())
        assert resp.status_code == 404, tail
        assert resp.json()["code"] == "not_found"


async def test_documents_are_404_until_they_exist(api: Api) -> None:
    await api.create()
    for tail in ("/brief", "/plan", "/spec"):
        resp = await api.get(tail)
        assert resp.status_code == 404, tail
        assert resp.json()["code"] == "not_found"
    versions = await api.get("/versions")
    assert versions.status_code == 200 and versions.json() == {"versions": []}


async def test_the_session_is_owned_by_its_creator(api: Api, state: AppState) -> None:
    await api.create()
    async with state.store.begin("tenant-a") as uow:
        mine = await uow.sessions.list_for_owner("user-1")
    assert [s.id for s in mine] == [api.session_id]
    assert mine[0].tenant_id == "tenant-a"
