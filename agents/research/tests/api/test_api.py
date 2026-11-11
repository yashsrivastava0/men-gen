import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from mde_research_agent.api import create_app
from mde_research_agent.api.sse import event_stream
from support.compose import build_service
from mde_research_agent.config import ResearchSettings
from support.fakes import FakeWorld

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "exports"
H = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}


def export(name: str = "quick-yoga-studio-bengaluru") -> dict[str, Any]:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def make(world: FakeWorld | None = None):
    world = world or FakeWorld(provider_content_every=3)
    svc = build_service(world=world, as_of=date(2026, 11, 11))
    app = create_app(svc, settings=ResearchSettings())
    return world, svc, app


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H
    )


async def finish(svc, run_id: str) -> None:
    await svc.wait(run_id, 60)


async def test_run_lifecycle_snapshot_result_latest_review_reject() -> None:
    _, svc, app = make()
    async with client(app) as c:
        r = await c.post("/v1/research/runs", json={"brief_export": export()})
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["events_url"].endswith(f"/runs/{run['run_id']}/events")
        await finish(svc, run["run_id"])
        snap = (await c.get(f"/v1/research/runs/{run['run_id']}")).json()
        assert (
            snap["status"] == "complete"
            and snap["load_class"] == "focused"
            and snap["targets"]
            and snap["feed"]
            and snap["last_seq"] > 20
        )
        assert (
            snap["content_hash"]
            and snap["counters"]["targets_done"] == snap["counters"]["targets_total"]
        )
        res = (await c.get(f"/v1/research/runs/{run['run_id']}/result")).json()
        assert res["schema"] == "research.v4" and res["items"] and res["brief_hash"]
        latest = await c.get(f"/v1/research/programs/{run['program_id']}/latest")
        assert (
            latest.status_code == 200
            and latest.json() == res
            and "attachment" in latest.headers["content-disposition"]
        )
        rev = (await c.get(f"/v1/research/programs/{run['program_id']}/review")).json()
        assert (
            len(rev["items"]) == len(res["items"])
            and rev["items"][0]["lane"]
            and rev["items"][0]["rejected"] is False
        )
        item_id = rev["items"][0]["id"]
        assert (
            await c.post(
                f"/v1/research/runs/{run['run_id']}/items/{item_id}/reject",
                json={"reason": "wrong"},
            )
        ).json() == {"item_id": item_id, "rejected": True}
        rev2 = (await c.get(f"/v1/research/programs/{run['program_id']}/review")).json()
        assert rev2["items"][0]["rejected"] is True and "withdrawn" in rev2["items"][0]["flags"]
        assert (
            await c.get(f"/v1/research/programs/{run['program_id']}/latest")
        ).json() == res  # the committed file never changes
        assert (
            await c.post(f"/v1/research/runs/{run['run_id']}/items/RI-999/reject", json={})
        ).status_code == 404


async def test_auth_tenant_isolation_and_error_shapes() -> None:
    _, svc, app = make()
    async with client(app) as c:
        run = (await c.post("/v1/research/runs", json={"brief_export": export()})).json()
        await finish(svc, run["run_id"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as anon:
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                r = await anon.get(f"/v1/research/runs/{run['run_id']}", headers=headers)
                assert r.status_code == 401 and r.json()["code"] == "unauthorized"
            assert (await anon.get("/healthz")).json() == {"ok": True}
        other = await c.get(
            f"/v1/research/runs/{run['run_id']}", headers={**H, "X-Tenant-Id": "t2"}
        )
        assert other.status_code == 404 and other.json()["code"] == "not_found"
        assert (await c.get("/v1/research/runs/rrn_nope")).status_code == 404
        bad = await c.post("/v1/research/runs", json={"nope": 1})
        assert bad.status_code == 422 and bad.json()["code"] == "bad_request"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d["brief"].update(_hash="0" * 64), "hash_mismatch"),
        (lambda d: d["export"].pop("approved_baseline"), "not_approved"),
        (lambda d: d["export"].update(format_version=1), "not_format_2"),
        (lambda d: d["consumer_views"].pop("research"), "missing_research_view"),
    ],
)
async def test_a_changed_or_unapproved_export_is_refused_with_a_plain_message(
    mutate: Any, code: str
) -> None:
    _, _, app = make()
    data = export()
    mutate(data)
    async with client(app) as c:
        r = await c.post("/v1/research/runs", json={"brief_export": data})
    assert r.status_code == 422 and r.json()["code"] == code and r.json()["message"]


async def test_one_run_per_program_at_a_time_then_the_next_is_allowed() -> None:
    world = FakeWorld()
    world.gate = asyncio.Event()
    _, svc, app = make(world)
    async with client(app) as c:
        first = (
            await c.post(
                "/v1/research/runs", json={"program_id": "prog_x", "brief_export": export()}
            )
        ).json()
        await asyncio.sleep(0.05)
        second = await c.post(
            "/v1/research/runs", json={"program_id": "prog_x", "brief_export": export()}
        )
        assert (
            second.status_code == 409
            and second.json()["code"] == "run_active"
            and second.json()["run_id"] == first["run_id"]
        )
        other_program = await c.post(
            "/v1/research/runs", json={"program_id": "prog_y", "brief_export": export()}
        )
        assert other_program.status_code == 201
        world.gate.set()
        await finish(svc, first["run_id"])
        await finish(svc, other_program.json()["run_id"])
        third = await c.post(
            "/v1/research/runs", json={"program_id": "prog_x", "brief_export": export()}
        )
        assert third.status_code == 201
        await finish(svc, third.json()["run_id"])


async def test_skip_stops_spending_and_commits_what_is_verified() -> None:
    world = FakeWorld()
    world.gate = asyncio.Event()
    _, svc, app = make(world)
    async with client(app) as c:
        run = (await c.post("/v1/research/runs", json={"brief_export": export()})).json()
        await asyncio.sleep(0.05)
        assert (await c.post(f"/v1/research/runs/{run['run_id']}/skip")).status_code == 202
        world.gate.set()
        await finish(svc, run["run_id"])
        snap = (await c.get(f"/v1/research/runs/{run['run_id']}")).json()
        assert snap["status"] == "skipped" and snap["result_status"] == "skipped"
        events = await svc.store.events_after(run["run_id"], 0)
        assert events[-1].type == "run.skipped" and [e.seq for e in events] == list(
            range(1, len(events) + 1)
        )
        res = (await c.get(f"/v1/research/runs/{run['run_id']}/result")).json()
        assert res["status"] == "skipped" and all(g["reason"] != "complete" for g in res["gaps"])
        assert (
            await c.post(f"/v1/research/runs/{run['run_id']}/skip")
        ).status_code == 202  # idempotent after the end


async def test_cancelling_one_topic_records_a_gap_and_the_rest_continue() -> None:
    world = FakeWorld()
    world.gate = asyncio.Event()
    _, svc, app = make(world)
    async with client(app) as c:
        run = (await c.post("/v1/research/runs", json={"brief_export": export()})).json()
        await asyncio.sleep(0.05)
        snap = (await c.get(f"/v1/research/runs/{run['run_id']}")).json()
        target = snap["targets"][1]["id"]
        assert (
            await c.post(f"/v1/research/runs/{run['run_id']}/targets/{target}/cancel")
        ).json() == {"cancelled": True}
        world.gate.set()
        await finish(svc, run["run_id"])
        res = (await c.get(f"/v1/research/runs/{run['run_id']}/result")).json()
        assert any(
            g["target_id"] == target and g["reason"] == "user_cancelled" for g in res["gaps"]
        )
        events = await svc.store.events_after(run["run_id"], 0)
        assert any(e.type == "target.cancelled" and e.target_id == target for e in events)
        cancel_seq = next(
            e.seq for e in events if e.type == "target.cancelled" and e.target_id == target
        )
        assert not any(
            e.type in ("query.issued", "fact.verified")
            and e.target_id == target
            and e.seq > cancel_seq
            for e in events
        )
        assert res["status"] in ("complete", "partial")


async def test_events_replay_and_resume_lose_nothing() -> None:
    _, svc, app = make()
    async with client(app) as c:
        run = (await c.post("/v1/research/runs", json={"brief_export": export()})).json()
        await finish(svc, run["run_id"])
        stored = await svc.store.events_after(run["run_id"], 0)

        async def active() -> bool:
            return False

        frames = [f async for f in event_stream(svc.store, run["run_id"], 0, is_active=active)]
        seqs = [int(f.split("\n")[0].removeprefix("id: ")) for f in frames if f.startswith("id: ")]
        assert frames[0] == "retry: 3000\n\n" and seqs == [e.seq for e in stored]
        resumed = [f async for f in event_stream(svc.store, run["run_id"], 10, is_active=active)]
        assert [
            int(f.split("\n")[0].removeprefix("id: ")) for f in resumed if f.startswith("id: ")
        ] == [e.seq for e in stored if e.seq > 10]
        r = await c.get(f"/v1/research/runs/{run['run_id']}/events", headers={"Last-Event-ID": "5"})
        assert r.headers["content-type"].startswith("text/event-stream")
        got = [
            int(line.removeprefix("id: "))
            for line in r.text.splitlines()
            if line.startswith("id: ")
        ]
        assert got == [e.seq for e in stored if e.seq > 5]
        data = [
            json.loads(line.removeprefix("data: "))
            for line in r.text.splitlines()
            if line.startswith("data: ")
        ]
        assert data[-1]["type"] == "run.finished" and data[0]["schema"] == "research.event.v1"


async def test_events_never_carry_page_text_or_quotes() -> None:
    _, svc, app = make()
    async with client(app) as c:
        run = (await c.post("/v1/research/runs", json={"brief_export": export()})).json()
        await finish(svc, run["run_id"])
    evidence = await svc.store.get_evidence(run["run_id"])
    assert evidence is not None
    blob = " ".join(
        json.dumps(e.model_dump(mode="json"), ensure_ascii=False)
        for e in await svc.store.events_after(run["run_id"], 0)
    )
    assert not any(len(a["quote"]) > 30 and a["quote"] in blob for a in evidence["atoms"])


async def test_bodies_are_limited_and_cors_is_explicit() -> None:
    _, _, app = make()
    async with client(app) as c:
        big = await c.post(
            "/v1/research/runs/rrn_x/skip",
            content=b"x" * 300_000,
            headers={"Content-Type": "application/json"},
        )
        assert big.status_code == 413 and big.json()["code"] == "too_large"
        pre = await c.options(
            "/v1/research/runs",
            headers={
                "Origin": "http://127.0.0.1:8110",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert (
            pre.status_code == 200
            and pre.headers["access-control-allow-origin"] == "http://127.0.0.1:8110"
        )
        evil = await c.options(
            "/v1/research/runs",
            headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in evil.headers


async def test_config_reports_booleans_and_never_a_key() -> None:
    _, _, app = make()
    async with client(app) as c:
        cfg = (await c.get("/v1/research/config")).json()
    assert (
        cfg["mode"] == "mock"
        and cfg["search_provider"] == "fake"
        and set(cfg["keys"]) == {"tavily", "serper"}
    )
    assert all(isinstance(v, bool) for v in cfg["keys"].values())
    assert "key" not in json.dumps({k: v for k, v in cfg.items() if k != "keys"}).lower()
