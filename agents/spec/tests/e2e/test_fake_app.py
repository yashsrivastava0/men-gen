"""The test-only fake app: it builds, answers its flag endpoint, honours every flag, and the real app has no such endpoint."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

Snap = dict[str, Any]


def args(module: ModuleType, **kw: Any) -> Any:
    base = ["--delay", "0"]
    for key, value in kw.items():
        flag = "--" + key.replace("_", "-")
        base += [flag] if value is True else [flag, str(value)]
    return module.parser().parse_args(base)


def client_for(module: ModuleType, root: Path, **kw: Any) -> TestClient:
    return TestClient(module.build_app(args(module, **kw), root))


def test_default_port_and_flag_endpoint(fake_module: ModuleType, tmp_path: Path) -> None:
    assert fake_module.parser().parse_args([]).port == 8150
    with client_for(fake_module, tmp_path) as c:
        body = c.get("/api/fake").json()
        assert body["fake"] is True and "TEST FAKE" in body["note"]
        health = c.get("/api/health").json()
        assert health["fake"] is True and health["ready"] is True
        assert c.get("/").status_code == 200  # the real front end is served beside it


def test_not_ready_refuses_live_runs_and_names_settings(
    fake_module: ModuleType, tmp_path: Path
) -> None:
    with client_for(fake_module, tmp_path, not_ready=True) as c:
        health = c.get("/api/health").json()
        assert health["ready"] is False and any("API_KEY" in r for r in health["reasons"])
        res = c.post(
            "/api/runs", json={"compass_id": "quick-yoga-studio-bengaluru", "offline": False}
        )
        assert res.status_code == 503 and res.json()["error"]["code"] == "not_ready"
        assert (
            c.post(
                "/api/runs", json={"compass_id": "quick-yoga-studio-bengaluru", "offline": True}
            ).status_code
            == 202
        )


def test_a_live_request_on_the_fake_never_costs_anything(
    fake_module: ModuleType, tmp_path: Path, waiter: Callable[..., Snap]
) -> None:
    with client_for(fake_module, tmp_path) as c:
        rid = c.post(
            "/api/runs", json={"compass_id": "quick-yoga-studio-bengaluru", "offline": False}
        ).json()["run_id"]
        snap = waiter(c, rid, ("awaiting_g2", "failed", "partial"))
        assert snap["status"] == "awaiting_g2", snap.get("note")
        assert c.get("/api/budget").json()["spent_micros"] == 0


def test_total_and_spent_set_the_budget(fake_module: ModuleType, tmp_path: Path) -> None:
    with client_for(fake_module, tmp_path, total="0.05", spent="0.02") as c:
        b = c.get("/api/budget").json()
        assert (
            b["cap_micros"] == 50_000
            and b["spent_micros"] == 20_000
            and b["remaining_micros"] == 30_000
        )
        res = c.post(
            "/api/runs", json={"compass_id": "thorough-freight-fleet-india", "offline": False}
        )
        assert res.status_code == 422 and res.json()["error"]["code"] == "refused_estimate"


def test_seed_leaves_a_history_in_every_state(fake_module: ModuleType, tmp_path: Path) -> None:
    with client_for(fake_module, tmp_path, seed=4) as c:
        runs = c.get("/api/runs").json()["runs"]
        assert sorted(r["status"] for r in runs) == [
            "approved",
            "awaiting_g2",
            "partial",
            "paused_g15",
        ]


def test_slow_runs_can_be_watched(fake_module: ModuleType, tmp_path: Path) -> None:
    with client_for(fake_module, tmp_path, slow=True, delay=0.15) as c:
        rid = c.post(
            "/api/runs", json={"compass_id": "quick-yoga-studio-bengaluru", "offline": True}
        ).json()["run_id"]
        seen = set()
        end = time.monotonic() + 60
        while time.monotonic() < end:
            snap = c.get(f"/api/runs/{rid}").json()
            seen.add(snap["status"])
            if snap["status"] == "awaiting_g2":
                break
            time.sleep(0.03)
        assert "running" in seen and "awaiting_g2" in seen


def test_the_real_app_has_no_fake_endpoint_and_nothing_in_src_reaches_the_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "real"))
    from mde_spec_agent.api import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/fake").status_code == 404
        assert c.get("/api/health").json()["fake"] is False
    src = Path(__file__).resolve().parents[2] / "src"
    offenders = [p for p in src.rglob("*.py") if "fake_app" in p.read_text(encoding="utf-8")]
    assert offenders == []
