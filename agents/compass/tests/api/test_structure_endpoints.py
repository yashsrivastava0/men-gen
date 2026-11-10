"""The structure routes, the size bands and the export over HTTP (ADR 0017, S6): httpx ASGITransport over the scripted
service, which holds one small valid clinic app for every session."""

from __future__ import annotations

import json

import httpx

from mde_compass.api.schemas import ErrorBody
from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app
from mde_compass.catalog.size_bands import load_size_bands

P = "/v1/compass"


async def test_the_structure_page_returns_modules_flows_entities_matrix_and_the_band(
    client: httpx.AsyncClient, session_id: str
) -> None:
    r = await client.get(f"{P}/sessions/{session_id}/structure")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id and body["drafted"] and body["enabled"]
    assert body["band"]["id"] == "medium" and body["band"]["screens"] == [20, 30]
    assert body["counts"]["modules"] == len(body["modules"]) >= 3
    assert body["modules"][0]["screens"] and body["flows"] and body["entities"]
    assert len(body["matrix"]["rows"]) == len(body["modules"])


async def test_the_structure_page_follows_the_language_and_rejects_an_unknown_one(
    client: httpx.AsyncClient, session_id: str
) -> None:
    en = (await client.get(f"{P}/sessions/{session_id}/structure?lang=en")).json()
    hi = (await client.get(f"{P}/sessions/{session_id}/structure?lang=hi")).json()
    assert hi["language"] == "hi" and hi["band"]["label"] != en["band"]["label"]
    assert (await client.get(f"{P}/sessions/{session_id}/structure?lang=fr")).status_code == 422
    assert (
        await client.get(f"{P}/sessions/{session_id}/structure?lang=english")
    ).status_code == 422


async def test_the_outline_is_ids_and_names_only_and_a_slice_is_one_module(
    client: httpx.AsyncClient, session_id: str
) -> None:
    outline = (await client.get(f"{P}/sessions/{session_id}/structure/outline")).json()
    assert {"counts", "modules", "entities", "flows"} <= set(outline)
    module_id = outline["modules"][0]["id"]
    assert all({"id", "name"} <= set(s) for s in outline["modules"][0]["screens"])
    r = await client.get(f"{P}/sessions/{session_id}/structure/modules/{module_id}")
    assert r.status_code == 200
    sl = r.json()
    assert sl["module"]["id"] == module_id and sl["screens"]
    assert len(json.dumps(sl)) // 4 <= 1500  # a slice fits the budget of one downstream call


async def test_an_unknown_module_session_or_tenant_is_not_found(
    client: httpx.AsyncClient, session_id: str
) -> None:
    r = await client.get(f"{P}/sessions/{session_id}/structure/modules/MOD-999")
    assert r.status_code == 404 and ErrorBody.model_validate(r.json()).code == "not_found"
    other = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t2", "X-User-Id": "u9"}
    for path in (
        f"/sessions/{session_id}/structure",
        f"/sessions/{session_id}/structure/outline",
        f"/sessions/{session_id}/structure/modules/MOD-001",
        f"/sessions/{session_id}/export",
    ):
        assert (await client.get(P + path, headers=other)).status_code == 404, path
    assert (await client.get(f"{P}/sessions/ses_missing/structure")).status_code == 404


async def test_the_export_is_format_two_and_carries_the_structure_as_a_download(
    client: httpx.AsyncClient, session_id: str
) -> None:
    r = await client.get(f"{P}/sessions/{session_id}/export")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert f"compass-{session_id}.json" in r.headers["content-disposition"]
    body = r.json()
    assert (
        body["export"]["format"] == "compass-full-output" and body["export"]["format_version"] == 2
    )
    structure = body["structure"]
    assert structure["band"] == "medium" and structure["slices"] and structure["outline"]
    assert structure["research"]["depth"] == "standard"


async def test_the_size_bands_are_public_static_data_straight_from_the_catalog(
    settings: ApiSettings,
) -> None:
    # no token, tenant or user header at all: like the strings, the numbers of each size are not tenant data
    app = create_app(ScriptedService(), settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        r = await anon.get(f"{P}/size-bands")
        assert (
            await anon.get(f"{P}/sessions")
        ).status_code == 401  # the client really is anonymous
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    body = r.json()
    data = load_size_bands()
    assert [b["id"] for b in body["bands"]] == list(data.bands)
    assert body["tier_to_band"] == data.tier_to_band
    by_id = {b["id"]: b for b in body["bands"]}
    assert by_id["small"]["screens"][0] == 8 and by_id["medium"]["screens"] == [20, 30]
    assert by_id["large"]["open_upper"] is True and by_id["large"]["review"] == "hard"
    assert [by_id[b]["research"]["depth"] for b in ("small", "medium", "large")] == [
        "light",
        "standard",
        "deep",
    ]
