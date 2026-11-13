"""Health, budget, profiles, config, and the shape of every error."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mde_spec_agent.service import SpecService


def test_health_has_the_documented_fields(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert set(body) == {"ready", "mode", "reasons", "fake", "version"}
    assert body["fake"] is False  # always false in the real app
    assert body["mode"] in ("live", "offline_only")
    assert isinstance(body["reasons"], list) and all(isinstance(r, str) for r in body["reasons"])


def test_health_names_missing_settings_by_name_only(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert (
        body["ready"] is False and body["mode"] == "offline_only"
    )  # the test settings have no key
    assert any("_API_KEY" in r for r in body["reasons"])
    assert not any("sk-" in r for r in body["reasons"])


def test_budget_shape_and_default_cap(client: TestClient) -> None:
    b = client.get("/api/budget").json()
    assert set(b) == {
        "period_id",
        "cap_micros",
        "tripwire_micros",
        "spent_micros",
        "reserved_micros",
        "remaining_micros",
        "state",
    }
    assert (b["cap_micros"], b["tripwire_micros"], b["spent_micros"]) == (5_000_000, 4_500_000, 0)
    assert b["remaining_micros"] == 5_000_000 and b["state"] == "ok"


def test_profiles_list_with_unset_fields_named(client: TestClient) -> None:
    profiles = client.get("/api/profiles").json()["profiles"]
    assert {"weak-default", "standard-default"} <= {p["id"] for p in profiles}
    weak = next(p for p in profiles if p["id"] == "weak-default")
    assert weak["capability_class"] == "weak"
    assert weak["k"] >= 1 and weak["whole_pack_tokens"] > 0 and weak["effective_window"] > 0
    assert "effective_context_tokens" in weak["unset"]


def test_config_is_read_only_numbers(client: TestClient) -> None:
    cfg = client.get("/api/config").json()
    assert cfg["numbers"]["budgets"]["task_read_budget"] == 28000
    assert cfg["money"]["cap_micros"] == 5_000_000
    assert client.post("/api/config", json={}).status_code == 405


def test_errors_use_one_shape(client: TestClient) -> None:
    for res in (
        client.get("/api/runs/run_NOPE00000000000000000000"),  # well formed, not there
        client.get("/api/runs/not-a-run-id"),  # not an id at all
        client.get("/api/nothing-here"),
    ):
        assert res.status_code == 404
        err = res.json()["error"]
        assert err["code"] == "not_found" and err["message"]


def test_invalid_body_is_bad_request(client: TestClient) -> None:
    res = client.post("/api/runs", json={"offline": True})  # no compass_id
    assert res.status_code == 400 and res.json()["error"]["code"] == "bad_request"
    res = client.post(
        "/api/runs", content=b"not json", headers={"content-type": "application/json"}
    )
    assert res.status_code == 400


def test_service_reads_run_history_empty(service: SpecService, client: TestClient) -> None:
    assert client.get("/api/runs").json() == {"runs": []}
