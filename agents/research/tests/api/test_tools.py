"""The Tools: the provider bake-off from the front end (offline doubles; every cent booked)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
from support.compose import build_app_test_service
from support.fakes import FakeSearch, FakeWorld

from mde_research_agent.api import create_app
from mde_research_agent.config import ResearchSettings
from mde_research_agent.tools import BakeoffTool

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "exports"
H = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}


def _providers(client: httpx.AsyncClient) -> list[FakeSearch]:
    out = []
    for name in ("tavily", "serper"):
        s = FakeSearch(FakeWorld(provider_content_every=3, search_cost_micros=4000))
        s.name = name
        out.append(s)
    return out


async def test_the_bakeoff_needs_its_word_runs_books_every_cent_and_keeps_the_result(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copy(FIX / "quick-yoga-studio-bengaluru.json", inputs / "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    svc.bakeoff = BakeoffTool(svc, _providers)
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = create_app(svc, settings=ResearchSettings(), ui_path=ui, sweep_seconds=3600)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H) as c:
        before = (await c.get("/api/tools/bakeoff")).json()
        assert before["state"] == "idle" and before["plan"]["queries"] > 0 and "search_usd" in before["plan"]
        assert (await c.post("/api/tools/bakeoff", json={"confirm": "no"})).status_code == 409
        assert (await c.post("/api/tools/bakeoff", json={"confirm": "RUN"})).status_code == 202
        for _ in range(200):
            st = (await c.get("/api/tools/bakeoff")).json()
            if st["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        assert st["state"] == "done", st
        res = st["result"]
        assert {s["provider"] for s in res["summaries"]} == {"tavily", "serper"} and res["recommended"] in ("tavily", "serper")
        spend = (await c.get("/api/spend")).json()["record"]["entries"]
        entry = next(e for e in spend if e["kind"] == "bakeoff")
        assert entry["status"] == "done" and entry["total_micros"] > 0
        assert any(k.startswith("search.") for k in entry["by_step"])
        assert (tmp_path / "data" / "bakeoff.json").is_file()
    await svc.aclose()
