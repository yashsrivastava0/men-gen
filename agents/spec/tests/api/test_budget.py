"""The money rules at the API: the preview costs nothing, an over-budget estimate is refused before anything is
created, a tripwire or an exhausted budget blocks live runs, a missing key is named by name only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mde_spec_agent.service import SpecService


def test_preview_is_zero_cost_and_makes_no_model_call(
    service: SpecService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("the preview must not build a gateway or call a model")

    monkeypatch.setattr(service, "_gateway", boom)
    plan = service.plan_input(compass_id="synth:small", offline=False)
    est = plan.estimate
    assert est["low_micros"] and est["high_micros"] and est["low_micros"] <= est["high_micros"]
    assert plan.preview["calls_by_step"]["screens"] >= 1
    assert service.budget()["spent_micros"] == 0


def test_preview_endpoint_shape(client: TestClient, start) -> None:  # type: ignore[no-untyped-def]
    snap = start("quick-yoga-studio-bengaluru")
    pv = client.get(f"/api/runs/{snap['id']}/preview").json()
    assert set(pv["profile"]) >= {"id", "label", "why"}
    assert pv["inventory_count"] > 0
    assert {"name", "screens", "note"} <= set(pv["phases"][0])
    assert 0 < pv["estimate"]["low_micros"] <= pv["estimate"]["high_micros"]
    assert pv["calls_by_step"]["plan"] == 1
    assert snap["spend_micros"] == 0  # an offline run costs nothing


def test_estimate_above_remaining_budget_is_refused_with_a_fix(make_service) -> None:  # type: ignore[no-untyped-def]
    svc = make_service(spec_live_budget_usd="0.01", spec_tripwire_usd="0.009")
    from fastapi.testclient import TestClient as TC

    from mde_spec_agent.api import create_app

    with TC(create_app(svc, settings=svc.settings)) as c:
        res = c.post("/api/runs", json={"compass_id": "synth:small", "offline": False})
        assert res.status_code == 422
        err = res.json()["error"]
        assert err["code"] == "refused_estimate" and err["message"] and err["fix"]
        assert err["high_micros"] > err["remaining_micros"]
        assert c.get("/api/runs").json() == {"runs": []}  # nothing was created
        # the same input runs offline: an offline run has no cost to refuse
        assert (
            c.post("/api/runs", json={"compass_id": "synth:small", "offline": True}).status_code
            == 202
        )


def test_live_run_without_keys_is_not_ready_and_names_settings(client: TestClient) -> None:
    res = client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": False})
    assert res.status_code == 503
    err = res.json()["error"]
    assert err["code"] == "not_ready" and err["fix"]
    assert err["missing"] and any("_API_KEY" in m for m in err["missing"])
    assert client.get("/api/runs").json() == {"runs": []}


def _spend(service: SpecService, micros: int) -> None:
    res = service.record.reserve(micros, label="test")
    service.record.commit(res, micros)


def test_exhausted_budget_blocks_live_runs(service: SpecService, client: TestClient) -> None:
    _spend(service, service.record.cap_micros)
    assert client.get("/api/budget").json()["state"] == "exhausted"
    res = client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": False})
    assert res.status_code == 409 and res.json()["error"]["code"] == "budget_blocked"
    # offline runs stay possible
    assert (
        client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": True}).status_code
        == 202
    )


def test_tripwire_blocks_live_runs_too(service: SpecService, client: TestClient) -> None:
    _spend(service, service.record.tripwire_micros)
    b = client.get("/api/budget").json()
    assert b["state"] == "tripwire" and b["remaining_micros"] > 0
    res = client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": False})
    assert res.status_code == 409 and res.json()["error"]["code"] == "budget_blocked"


def test_budget_endpoint_reflects_spend(service: SpecService, client: TestClient) -> None:
    _spend(service, 1_234_000)
    b = client.get("/api/budget").json()
    assert b["spent_micros"] == 1_234_000 and b["remaining_micros"] == 5_000_000 - 1_234_000


# ---- the preview BEFORE a run exists (the owner sees the cost, then says yes) ------------------------------------------


def test_preview_before_a_run_creates_nothing_and_calls_no_model(
    service: SpecService, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a preview must not build a gateway or call a model")

    monkeypatch.setattr(service, "_gateway", boom)
    res = client.post("/api/preview", json={"compass_id": "synth:small", "offline": True})
    assert res.status_code == 200
    pv = res.json()
    assert pv["mode"] == "offline" and pv["inventory_count"] > 0
    assert 0 < pv["estimate"]["low_micros"] <= pv["estimate"]["high_micros"]
    assert pv["remaining_micros"] == service.record.cap_micros
    assert client.get("/api/runs").json() == {"runs": []}  # no run was created
    assert service.budget()["spent_micros"] == 0


def test_preview_says_why_a_live_run_could_not_start_without_raising(client: TestClient) -> None:
    pv = client.post("/api/preview", json={"compass_id": "synth:tiny", "offline": False}).json()
    assert pv["mode"] == "live"
    assert pv["live"]["allowed"] is False
    blocked = pv["live"]["blocked"]
    assert blocked["code"] == "not_ready" and blocked["fix"]
    assert any("_API_KEY" in m for m in blocked["missing"])  # names only, never a value
    assert client.get("/api/runs").json() == {"runs": []}


def test_preview_reports_an_estimate_the_money_cannot_cover(make_service) -> None:  # type: ignore[no-untyped-def]
    svc = make_service(spec_live_budget_usd="0.01", spec_tripwire_usd="0.009")
    from mde_spec_agent.api import create_app

    with TestClient(create_app(svc, settings=svc.settings)) as c:
        pv = c.post("/api/preview", json={"compass_id": "synth:small", "offline": False}).json()
        assert pv["refused"] is True and pv["refusal_message"]
        assert pv["live"]["allowed"] is False
        assert pv["live"]["blocked"]["code"] == "refused_estimate"
        assert pv["live"]["blocked"]["high_micros"] > pv["live"]["blocked"]["remaining_micros"]
        assert c.get("/api/runs").json() == {"runs": []}


def test_preview_reports_an_exhausted_budget(service: SpecService, client: TestClient) -> None:
    _spend(service, service.record.cap_micros)
    pv = client.post("/api/preview", json={"compass_id": "synth:tiny", "offline": False}).json()
    assert pv["budget_state"] == "exhausted"
    assert pv["live"]["blocked"]["code"] == "budget_blocked"
    assert pv["remaining_micros"] == 0


def test_preview_of_an_unknown_input_is_a_plain_400(client: TestClient) -> None:
    res = client.post("/api/preview", json={"compass_id": "no-such-export", "offline": True})
    assert res.status_code in (400, 404) and res.json()["error"]["message"]
