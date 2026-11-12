"""Provider credits in the standalone app: ``GET /api/credits``, ``POST /api/credits/refresh`` and ``/baseline``, the
preflight lines (information only), Tavily's own count over a mock transport (throttle, 401, 429), and what a run
records. Over the offline doubles: no network, no key, the ledger lives in the test's own folder."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from support.compose import build_app_test_service

from mde_core.clock import FakeClock
from mde_research_agent.adapters.search.metered import MeteredSearch
from mde_research_agent.api import create_app
from mde_research_agent.config import ResearchSettings
from mde_research_agent.service import ResearchService

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "exports"
H = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
KEY = "tvly-NOT-A-REAL-KEY-0003"  # secret-scan: allow
USAGE = {
    "key": {"usage": 96, "limit": None},
    "account": {"current_plan": "Researcher", "plan_usage": 96, "plan_limit": 1000},
}


def make_app(svc: ResearchService, tmp_path: Path, **settings: Any):
    ui = tmp_path / "ui"
    ui.mkdir(exist_ok=True)
    (ui / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    cfg = ResearchSettings(**{"tavily_api_key": None, "serper_api_key": None, **settings})
    return create_app(svc, settings=cfg, ui_path=ui, sweep_seconds=3600)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H
    )


def with_usage_endpoint(
    svc: ResearchService, respond: Any, *, key: str | None = KEY
) -> tuple[list[httpx.Request], FakeClock]:
    """Give the service a Tavily key and a client that answers ``GET /usage`` from ``respond`` (nothing else exists)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)

    svc.credits_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc.settings = ResearchSettings(
        tavily_api_key=SecretStr(key) if key else None, serper_api_key=None
    )
    clock = FakeClock(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))
    svc._mono = clock.monotonic
    return seen, clock


# ------------------------------------------------------------------------------------------------ the shape
async def test_credits_start_unknown_with_the_expected_need_per_size(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/credits")).json()
    assert body["enabled"] is True and body["per_run"] == [] and body["write_failed"] is False
    tavily, serper = body["providers"]
    assert (tavily["provider"], tavily["label"], tavily["unit"]) == ("tavily", "Tavily", "credit")
    assert tavily["level"] == "unknown" and tavily["used"] is None and tavily["remaining"] is None
    assert tavily["source"] == "counted" and tavily["can_check"] is True
    assert serper["provider"] == "serper" and serper["can_check"] is False
    # money-free: nothing in the body is a dollar amount
    assert "usd" not in str(body["providers"]).lower() and "micros" not in str(body["providers"])
    small = body["estimate_hint"]["small"]
    assert small["tavily"]["high"] == 41 and small["serper"]["mid"] == 26
    assert set(body["estimate_hint"]) == {"tiny", "small", "medium", "big"}
    await svc.aclose()


async def test_the_ledger_lives_in_the_services_own_data_folder(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    async with client(make_app(svc, tmp_path)) as c:
        await c.post(
            "/api/credits/baseline", json={"provider": "tavily", "kind": "used", "amount": 93}
        )
    assert (tmp_path / "data" / "provider-credits.json").is_file()
    await svc.aclose()


async def test_the_credit_routes_need_the_token(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    app = make_app(svc, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/api/credits")).status_code == 401
        assert (await c.post("/api/credits/refresh")).status_code == 401
        body = {"provider": "tavily", "kind": "used", "amount": 1}
        assert (await c.post("/api/credits/baseline", json=body)).status_code == 401
    await svc.aclose()


# ------------------------------------------------------------------------------------------------ the baseline
async def test_the_owners_baselines_give_used_and_remaining(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    async with client(make_app(svc, tmp_path)) as c:
        r1 = await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 93, "limit": 1000},
        )
        r2 = await c.post(
            "/api/credits/baseline",
            json={"provider": "serper", "kind": "remaining", "amount": 2389},
        )
        body = (await c.get("/api/credits")).json()
    assert r1.status_code == 200 and r2.status_code == 200
    tavily, serper = body["providers"]
    assert (tavily["used"], tavily["limit"], tavily["remaining"], tavily["level"]) == (
        93,
        1000,
        907,
        "ok",
    )
    assert tavily["baseline"]["kind"] == "used" and tavily["as_of"]
    assert (serper["remaining"], serper["used"], serper["level"]) == (2389, None, "ok")
    await svc.aclose()


async def test_a_baseline_is_validated(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    good = {"provider": "tavily", "kind": "used", "amount": 5, "limit": 10}
    async with client(make_app(svc, tmp_path)) as c:
        for patch in (
            {"provider": "bing"},
            {"kind": "balance"},
            {"amount": -1},
            {"amount": 1.5},
            {"amount": "93"},
            {"amount": True},
            {"amount": None},
            {"limit": -5},
            {"limit": 2.5},
            {"extra": 1},
        ):
            res = await c.post("/api/credits/baseline", json={**good, **patch})
            assert res.status_code == 422, patch
        assert (
            await c.post("/api/credits/baseline", json={"provider": "tavily"})
        ).status_code == 422
        # a figure that cannot be true says so in plain words
        res = await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 2000, "limit": 1000},
        )
        assert res.status_code == 409 and "more than the limit" in res.json()["message"]
        body = (await c.get("/api/credits")).json()
    assert body["providers"][0]["used"] is None  # nothing refused was saved
    await svc.aclose()


# ------------------------------------------------------------------------------------------------ Tavily's own count
async def test_refresh_stores_the_providers_reading_and_it_wins(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    seen, _ = with_usage_endpoint(svc, lambda _r: httpx.Response(200, json=USAGE))
    async with client(make_app(svc, tmp_path)) as c:
        await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 93, "limit": 1000},
        )
        res = await c.post("/api/credits/refresh")
    body = res.json()
    assert res.status_code == 200 and body["refresh"] == {
        "done": True,
        "throttled": False,
        "error": None,
    }
    tavily = body["providers"][0]
    assert (tavily["used"], tavily["limit"], tavily["source"], tavily["plan"]) == (
        96,
        1000,
        "provider",
        "Researcher",
    )
    assert tavily["read_at"] and body["check"]["error"] is None
    assert len(seen) == 1 and seen[0].headers["authorization"] == f"Bearer {KEY}"
    ledger_text = (tmp_path / "data" / "provider-credits.json").read_text(encoding="utf-8")
    assert KEY not in ledger_text  # only the fingerprint is kept
    await svc.aclose()


async def test_refresh_is_throttled_to_one_call_a_minute(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    seen, clock = with_usage_endpoint(svc, lambda _r: httpx.Response(200, json=USAGE))
    async with client(make_app(svc, tmp_path)) as c:
        first = (await c.post("/api/credits/refresh")).json()["refresh"]
        clock.advance(10)
        second = (await c.post("/api/credits/refresh")).json()
        via_get = (await c.get("/api/credits?refresh=1")).json()
        clock.advance(51)
        third = (await c.post("/api/credits/refresh")).json()["refresh"]
    assert first["done"] is True
    assert second["refresh"]["done"] is False and second["refresh"]["throttled"] is True
    assert second["refresh"]["wait_s"] == 50 and second["check"]["wait_s"] == 50
    assert via_get["providers"][0]["used"] == 96  # the answer is still the stored one
    assert third["done"] is True
    assert len(seen) == 2  # not three, not four
    await svc.aclose()


async def test_a_401_is_reported_not_raised_and_keeps_the_counted_figure(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    with_usage_endpoint(svc, lambda _r: httpx.Response(401, json={"detail": {"error": "bad key"}}))
    async with client(make_app(svc, tmp_path)) as c:
        await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 93, "limit": 1000},
        )
        res = await c.post("/api/credits/refresh")
    body = res.json()
    assert res.status_code == 200 and body["refresh"]["done"] is False
    assert body["refresh"]["error"] == "auth" and body["check"]["error"] == "auth"
    assert body["providers"][0]["used"] == 93 and body["providers"][0]["read_at"] is None
    await svc.aclose()


async def test_a_429_waits_as_long_as_retry_after_says(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    seen, clock = with_usage_endpoint(
        svc,
        lambda _r: httpx.Response(429, headers={"Retry-After": "300"}, json={"message": "slow"}),
    )
    async with client(make_app(svc, tmp_path)) as c:
        first = (await c.post("/api/credits/refresh")).json()["refresh"]
        clock.advance(120)  # past the ordinary minute, still inside Retry-After
        second = (await c.post("/api/credits/refresh")).json()["refresh"]
        clock.advance(181)
        third = (await c.post("/api/credits/refresh")).json()["refresh"]
    assert first["error"] == "rate_limited" and first["done"] is False
    assert second["throttled"] is True and second["wait_s"] == 180
    assert third["throttled"] is False and third["error"] == "rate_limited"
    assert len(seen) == 2
    await svc.aclose()


async def test_a_malformed_answer_is_reported_as_one(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    with_usage_endpoint(svc, lambda _r: httpx.Response(200, json={"hello": "world"}))
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.post("/api/credits/refresh")).json()
    assert body["refresh"]["error"] == "bad_response" and body["providers"][0]["used"] is None
    await svc.aclose()


async def test_nothing_is_asked_without_a_key_or_a_client(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)  # no client: a test service can never reach the network
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.post("/api/credits/refresh")).json()
        assert body["refresh"] == {"done": False, "throttled": False, "error": "not_available"}
    seen, _ = with_usage_endpoint(svc, lambda _r: httpx.Response(200, json=USAGE), key=None)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.post("/api/credits/refresh")).json()
    assert body["refresh"]["error"] == "not_available" and seen == []
    await svc.aclose()


async def test_an_unexpected_failure_never_reaches_the_caller(
    tmp_path: Path, monkeypatch: Any
) -> None:
    svc = build_app_test_service(tmp_path)

    async def boom() -> dict[str, Any]:
        raise RuntimeError("anything")

    monkeypatch.setattr(svc, "_refresh_credits", boom)
    assert await svc.refresh_credits() == {"done": False, "throttled": False, "error": "internal"}
    await svc.aclose()


async def test_the_app_checks_tavily_once_at_start(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    seen, _ = with_usage_endpoint(svc, lambda _r: httpx.Response(200, json=USAGE))
    app = make_app(svc, tmp_path)
    async with app.router.lifespan_context(app):
        import asyncio

        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.01)
    assert len(seen) == 1
    await svc.aclose()


# ------------------------------------------------------------------------------------------------ preflight
async def test_preflight_credit_lines_are_information_and_never_make_the_app_not_ready(
    tmp_path: Path,
) -> None:
    svc = build_app_test_service(tmp_path)
    app = make_app(svc, tmp_path, tavily_api_key=SecretStr(KEY))
    async with client(app) as c:
        before = (await c.get("/api/preflight")).json()
        await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 990, "limit": 1000},
        )
        after = (await c.get("/api/preflight")).json()
    line = next(x for x in after["checks"] if x["id"] == "credits:tavily")
    assert line["ok"] is True and line["fix"] == "" and line["level"] == "red"
    assert "990 of 1,000 credits used" in line["detail"] and "may stop early" in line["note"]
    assert not any(x["id"] == "credits:serper" for x in after["checks"])  # no key, no line
    quiet = next(x for x in before["checks"] if x["id"] == "credits:tavily")
    assert quiet["ok"] is True and quiet["note"] == "" and quiet["detail"] == "no count yet"
    assert after["ready"] == before["ready"]  # credits do not change readiness
    await svc.aclose()


async def test_low_credits_do_not_stop_a_run(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copy(FIX / "quick-yoga-studio-bengaluru.json", inputs / "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 1000, "limit": 1000},
        )
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        started = await c.post("/api/runs", json={"input_id": iid})
        assert started.status_code == 201  # exhausted credits warn; they never refuse
        await svc.wait(started.json()["run_id"], 120)
    await svc.aclose()


# ------------------------------------------------------------------------------------------------ what a run records
async def test_a_run_records_the_credits_it_was_billed(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copy(FIX / "quick-yoga-studio-bengaluru.json", inputs / "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    assert svc.credits is not None and svc.search is not None
    svc.search = MeteredSearch(
        svc.search, svc.credits, "tavily"
    )  # the real wiring, over the fake web
    async with client(make_app(svc, tmp_path)) as c:
        await c.post(
            "/api/credits/baseline",
            json={"provider": "tavily", "kind": "used", "amount": 93, "limit": 1000},
        )
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        run_id = (await c.post("/api/runs", json={"input_id": iid})).json()["run_id"]
        await svc.wait(run_id, 120)
        credits = (await c.get("/api/credits")).json()
        spend = (await c.get("/api/spend")).json()
        report = (await c.get(f"/v1/research/runs/{run_id}/report")).text
        result = (await c.get(f"/v1/research/runs/{run_id}/result")).json()
    row = await svc.get_row("t1", run_id)
    billed = row.meta["credits"]["tavily"]
    assert (
        billed > 0 and billed == result["budget"]["searches_used"]
    )  # the fake bills one credit a search
    assert (
        "credits" not in result["budget"] and "credits" not in result
    )  # research.json is untouched
    assert credits["per_run"][0]["run_id"] == run_id
    assert credits["per_run"][0]["credits"] == {"tavily": billed}
    assert (
        credits["providers"][0]["used"] == 93 + billed
        and credits["providers"][0]["source"] == "counted"
    )
    entry = next(e for e in spend["record"]["entries"] if e["run_id"] == run_id)
    assert entry["meta"]["credits"] == {"tavily": billed} and entry["meta"]["size_class"] == "small"
    assert f"- Search credits billed: Tavily {billed}" in report
    evidence = await svc.store.get_evidence(run_id)
    assert evidence is not None and evidence["credits"] == {"tavily": billed}
    await svc.aclose()


async def test_a_run_after_which_tavily_is_asked_stores_its_own_count(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copy(FIX / "quick-yoga-studio-bengaluru.json", inputs / "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    seen, _ = with_usage_endpoint(svc, lambda _r: httpx.Response(200, json=USAGE))
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        run_id = (await c.post("/api/runs", json={"input_id": iid})).json()["run_id"]
        await svc.wait(run_id, 120)
        import asyncio

        await asyncio.gather(*list(svc._background))  # the check that follows the run
        body = (await c.get("/api/credits")).json()
    assert len(seen) == 1 and body["providers"][0]["used"] == 96
    await svc.aclose()
