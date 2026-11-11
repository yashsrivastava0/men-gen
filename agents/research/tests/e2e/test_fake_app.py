"""The test-only fake app: it builds, shows its flag endpoint, lists the edge-case inputs, and the real app has no such endpoint."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import httpx

from mde_research_agent.api import create_app
from mde_research_agent.config import ResearchSettings

HERE = Path(__file__).resolve().parent
H = {"Authorization": "Bearer dev-token"}


def load_fake():
    spec = importlib.util.spec_from_file_location("fake_app_under_test", HERE / "fake_app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(**kw) -> argparse.Namespace:
    base = dict(
        empty=False,
        missing_folder=False,
        not_ready=False,
        slow=False,
        delay=0.0,
        max_concurrent=1,
        total="5.00",
        tripwire="4.50",
        spent=0.0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


async def test_the_fake_answers_its_flag_and_lists_the_edge_cases(tmp_path: Path) -> None:
    fake = load_fake()
    app = fake.make_app(args(), tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/api/fake")).json()["fake"] is True
        body = (await c.get("/api/inputs", headers=H)).json()
        pre = (await c.get("/api/preflight", headers=H)).json()
    assert [i["preview"]["size_class"] for i in body["inputs"]] == [
        "small",
        "small",
        "medium",
        "big",
    ]
    reasons = {s["name"]: s["reason"] for s in body["skipped"]}
    assert (
        reasons["notes.json"] == "not_json" and reasons["unrelated.json"] == "not_a_compass_export"
    )
    assert (
        reasons["old-format-1.json"] == "format_not_supported"
        and reasons["hash-mismatch.json"] == "hash_mismatch"
    )
    assert reasons["not-approved.json"] == "not_approved" and reasons["binary.json"] == "unreadable"
    assert any(
        "<img src=x onerror=alert(1)>" in i["preview"]["idea"]["summary"] for i in body["inputs"]
    )
    assert pre["ready"] is True
    await app.state.service.aclose()


async def test_not_ready_flag_refuses_starts_naming_the_variables(tmp_path: Path) -> None:
    fake = load_fake()
    app = fake.make_app(args(not_ready=True), tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        first = (await c.get("/api/inputs", headers=H)).json()["inputs"][0]["id"]
        r = await c.post("/api/runs", headers=H, json={"input_id": first})
        pre = (await c.get("/api/preflight", headers=H)).json()
    assert r.status_code == 409 and r.json()["code"] == "not_ready" and r.json()["missing"]
    assert pre["ready"] is False
    await app.state.service.aclose()


async def test_the_real_app_has_no_fake_endpoint(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    app = create_app(settings=ResearchSettings(), ui_path=ui, sweep_seconds=3600)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/api/fake")).status_code == 404
