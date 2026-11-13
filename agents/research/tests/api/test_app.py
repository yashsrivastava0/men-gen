"""The standalone app's own surface: inputs discovery, runs and the queue, the budget and spend record, preflight,
readiness, recovery and the same-origin rule. Everything runs over the offline doubles (``tests/support``)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from support.compose import build_app_test_service

from mde_research_agent.api import create_app
from mde_research_agent.config import ResearchSettings
from mde_research_agent.domain.events import Counters, Event
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import RunRow

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "exports"
H = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
YOGA = "quick-yoga-studio-bengaluru"
NASHIK = "standard-produce-marketplace-nashik"


def put(folder: Path, name: str, source: str = YOGA) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    shutil.copy(FIX / f"{source}.json", target)
    return target


def make_app(svc: ResearchService, tmp_path: Path):
    ui = tmp_path / "ui"
    ui.mkdir(exist_ok=True)
    (ui / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    return create_app(svc, settings=ResearchSettings(), ui_path=ui, sweep_seconds=3600)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H
    )


async def finish(svc: ResearchService, run_id: str) -> None:
    await svc.wait(run_id, 120)


# ----------------------------------------------------------------------------------------------- inputs
async def test_inputs_are_found_previewed_and_sorted_by_size(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "b-big-name.json", NASHIK)
    put(inputs, "a-small-name.json", YOGA)
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/inputs")).json()
    assert body["folder_exists"] is True and body["skipped"] == []
    cards = body["inputs"]
    assert [c["preview"]["size_class"] for c in cards] == [
        "small",
        "medium",
    ]  # by computed load, not file name
    assert cards[0]["name"] == "a-small-name.json" and len(cards[0]["short_hash"]) == 12
    p = cards[0]["preview"]
    assert p["load"]["class"] == "focused" and p["trust"]["on"] is False and "why" in p["trust"]
    assert p["topics"]["total"] > 0 and p["estimate"]["note"] == "estimate, not measured"
    await svc.aclose()


async def test_a_dropped_in_file_appears_without_a_restart_and_a_bad_one_gets_a_reason(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        assert (await c.get("/api/inputs")).json()["inputs"] == []
        put(inputs, "new.json")
        (inputs / "notes.json").write_text("this is not json", encoding="utf-8")
        (inputs / "other.json").write_text(json.dumps({"hello": 1}), encoding="utf-8")
        (inputs / "binary.json").write_bytes(b"\xff\xfe\x00\x80")
        (inputs / "README.md").write_text("ignored: not json", encoding="utf-8")
        body = (await c.get("/api/inputs")).json()
    assert [i["name"] for i in body["inputs"]] == ["new.json"]
    reasons = {s["name"]: s["reason"] for s in body["skipped"]}
    assert reasons == {
        "notes.json": "not_json",
        "other.json": "not_a_compass_export",
        "binary.json": "unreadable",
    }
    assert all(s["text"] for s in body["skipped"])  # a plain sentence for each
    await svc.aclose()


async def test_format_hash_and_approval_problems_are_listed_with_their_binding(
    tmp_path: Path,
) -> None:
    """An older format, a changed Brief and a missing approval are inputs with a flag, not skipped files (D-156)."""
    inputs = tmp_path / "inputs"
    data = json.loads((FIX / f"{YOGA}.json").read_text(encoding="utf-8"))
    v1 = json.loads(json.dumps(data))
    v1["export"]["format_version"] = 1
    mismatch = json.loads(json.dumps(data))
    mismatch["brief"]["_hash"] = "0" * 64
    unapproved = json.loads(json.dumps(data))
    unapproved["export"].pop("approved_baseline")
    inputs.mkdir()
    for name, obj in (
        ("v1.json", v1),
        ("mismatch.json", mismatch),
        ("unapproved.json", unapproved),
    ):
        (inputs / name).write_text(json.dumps(obj), encoding="utf-8")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/inputs")).json()
    assert body["skipped"] == []
    previews = {i["name"]: i["preview"] for i in body["inputs"]}
    assert {n: p["input_binding"] for n, p in previews.items()} == {
        "v1.json": "verified",
        "mismatch.json": "unverified_hash_mismatch",
        "unapproved.json": "unverified_no_baseline",
    }
    assert previews["v1.json"]["compass"]["format"] == 1
    for name in ("mismatch.json", "unapproved.json"):
        warning = next(
            w for w in previews[name]["warnings"] if w["code"] == previews[name]["input_binding"]
        )
        assert warning["text"]  # a plain sentence for the person
    assert not [w for w in previews["v1.json"]["warnings"] if w["code"].startswith("unverified")]
    await svc.aclose()


async def test_same_content_under_two_names_lists_once_and_symlinks_out_are_refused(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "one.json")
    put(inputs, "copy-of-one.json")
    outside = tmp_path / "outside.json"
    shutil.copy(FIX / f"{NASHIK}.json", outside)
    os.symlink(outside, inputs / "link.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/inputs")).json()
    assert len(body["inputs"]) == 1 and body["inputs"][0]["also_named"]
    assert [(s["name"], s["reason"]) for s in body["skipped"]] == [("link.json", "unsafe_path")]
    await svc.aclose()


async def test_a_file_over_25_mb_is_skipped_and_the_client_cannot_name_a_path(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    with (inputs / "huge.json").open("wb") as fh:
        fh.truncate(25 * 1024 * 1024 + 1)
    put(inputs, "ok.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/inputs")).json()
        assert {s["name"]: s["reason"] for s in body["skipped"]} == {"huge.json": "too_large"}
        for evil in ("..%2F..%2Fetc%2Fpasswd", "ok.json", "../outside", "%2Fetc%2Fhosts"):
            assert (await c.get(f"/api/inputs/{evil}")).status_code == 404
        assert (await c.post("/api/runs", json={"input_id": "../../x"})).status_code == 404
        assert (await c.post("/api/runs", json={"path": "/etc/hosts"})).status_code == 422
    await svc.aclose()


async def test_a_missing_inputs_folder_is_an_empty_state_not_a_crash(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path, inputs_dir=tmp_path / "nope")
    async with client(make_app(svc, tmp_path)) as c:
        body = (await c.get("/api/inputs")).json()
    assert (
        body["folder_exists"] is False and body["inputs"] == [] and body["folder"].endswith("nope")
    )
    await svc.aclose()


async def test_upload_validates_and_stores_in_the_uploads_folder_only(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        bad = (await c.post("/api/inputs/upload", content=b"not json")).json()
        assert bad["accepted"] is False and bad["reason"] == "not_json"
        good = (
            await c.post("/api/inputs/upload", content=(FIX / f"{YOGA}.json").read_bytes())
        ).json()
        assert good["accepted"] is True
        listed = (await c.get("/api/inputs")).json()["inputs"]
    assert listed[0]["name"].startswith("uploads/")
    assert list(inputs.iterdir()) == []  # the inputs folder is read-only for the app
    await svc.aclose()


# ----------------------------------------------------------------------------------------------- runs
async def test_a_run_from_an_input_is_recorded_in_the_spend_record_and_the_budget(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        started = await c.post("/api/runs", json={"input_id": iid})
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        await finish(svc, run_id)
        runs = (await c.get("/api/runs")).json()["runs"]
        spend = (await c.get("/api/spend")).json()
        budget = (await c.get("/api/budget")).json()
    assert runs[0]["run_id"] == run_id and runs[0]["status"] in ("complete", "partial")
    assert runs[0]["input_name"] == "yoga.json" and runs[0]["spend_micros"] > 0
    entry = next(e for e in spend["record"]["entries"] if e["run_id"] == run_id)
    assert entry["kind"] == "run" and entry["status"] in ("complete", "partial")
    assert any(k.startswith("search.") for k in entry["by_step"]) and entry["total_micros"] > 0
    assert entry["meta"]["size_class"] == "small"
    assert budget["spent_micros"] == entry["total_micros"] and budget["state"] == "ok"
    # the run kept its own copy of the input and its cassettes
    run_dir = tmp_path / "data" / "runs" / run_id
    assert (run_dir / "input.json.gz").is_file() and (run_dir / "research.json").is_file()
    assert (run_dir / "cassettes" / "search").is_dir()
    await svc.aclose()


async def test_a_second_start_for_the_same_input_returns_the_active_run(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        a = await c.post("/api/runs", json={"input_id": iid})
        b = await c.post("/api/runs", json={"input_id": iid})
        assert a.status_code == 201 and b.status_code == 200
        assert a.json()["run_id"] == b.json()["run_id"] and b.json()["created"] is False
        await finish(svc, a.json()["run_id"])
    await svc.aclose()


async def test_runs_queue_one_at_a_time_and_a_queued_run_can_be_cancelled(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json", YOGA)
    put(inputs, "nashik.json", NASHIK)
    svc = build_app_test_service(tmp_path, inputs_dir=inputs, max_concurrent=1)
    async with client(make_app(svc, tmp_path)) as c:
        cards = (await c.get("/api/inputs")).json()["inputs"]
        first = (await c.post("/api/runs", json={"input_id": cards[0]["id"]})).json()
        second = (await c.post("/api/runs", json={"input_id": cards[1]["id"]})).json()
        assert first["status"] in ("queued", "running") and first["queue_position"] is None
        assert second["status"] == "queued" and second["queue_position"] == 1
        cancelled = (await c.post(f"/api/runs/{second['run_id']}/cancel-queued")).json()
        assert cancelled["status"] == "cancelled"
        events = await svc.store.events_after(second["run_id"], 0)
        assert (
            events and events[-1].type == "run.failed"
        )  # a terminal event even for a cancelled queued run
        await finish(svc, first["run_id"])
    await svc.aclose()


async def test_a_queued_run_starts_by_itself_when_the_first_finishes(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json", YOGA)
    put(inputs, "nashik.json", NASHIK)
    svc = build_app_test_service(tmp_path, inputs_dir=inputs, max_concurrent=1)
    async with client(make_app(svc, tmp_path)) as c:
        cards = (await c.get("/api/inputs")).json()["inputs"]
        first = (await c.post("/api/runs", json={"input_id": cards[0]["id"]})).json()
        second = (await c.post("/api/runs", json={"input_id": cards[1]["id"]})).json()
        await finish(svc, first["run_id"])
        for _ in range(200):
            row = await svc.store.get_run("demo", second["run_id"])
            if row and row.status not in ("queued",):
                break
            await asyncio.sleep(0.05)
        await finish(svc, second["run_id"])
        statuses = {r["run_id"]: r["status"] for r in (await c.get("/api/runs")).json()["runs"]}
    assert statuses[first["run_id"]] in ("complete", "partial")
    assert statuses[second["run_id"]] in ("complete", "partial")
    await svc.aclose()


# ----------------------------------------------------------------------------------------------- readiness
async def test_a_service_that_is_not_ready_refuses_a_run_naming_what_to_set(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(
        tmp_path, inputs_dir=inputs, no_gateway=True, missing=["TAVILY_API_KEY or SERPER_API_KEY"]
    )
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        r = await c.post("/api/runs", json={"input_id": iid})
        pre = (await c.get("/api/preflight")).json()
    assert r.status_code == 409 and r.json()["code"] == "not_ready"
    assert r.json()["missing"] == ["TAVILY_API_KEY or SERPER_API_KEY"]
    assert pre["ready"] is False
    assert any(not ch["ok"] and ch["fix"] for ch in pre["checks"])
    assert (
        svc.queue == [] and await svc.store.active_runs() == []
    )  # nothing was started, nothing invented
    await svc.aclose()


async def test_preflight_lists_plain_checks_and_never_a_value(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        pre = (await c.get("/api/preflight")).json()
    ids = {c["id"] for c in pre["checks"]}
    assert {"inputs", "data", "store", "budget", "ui", "routes", "search"} <= ids
    assert all(set(c) == {"id", "label", "ok", "detail", "fix"} for c in pre["checks"])
    assert "sk-" not in json.dumps(pre)
    await svc.aclose()


# ----------------------------------------------------------------------------------------------- budget
async def test_at_the_tripwire_no_new_run_starts(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    assert svc.record is not None
    eid = svc.record.start_entry("probe", label="spent elsewhere")
    svc.record.add_cost(eid, "probe", 4_500_000)
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        r = await c.post("/api/runs", json={"input_id": iid})
        assert r.status_code == 409 and r.json()["code"] == "budget_tripwire"
        assert (await c.get("/api/budget")).json()["state"] == "tripwire"
    await svc.aclose()


async def test_restart_needs_the_word_keeps_history_and_is_refused_during_a_run(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    assert svc.record is not None
    first_period = svc.record.period["id"]
    eid = svc.record.start_entry("probe", label="a probe")
    svc.record.add_cost(eid, "probe", 1_230_000)
    async with client(make_app(svc, tmp_path)) as c:
        assert (await c.post("/api/budget/restart", json={"confirm": "yes"})).status_code == 409
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        run = (await c.post("/api/runs", json={"input_id": iid})).json()
        assert (await c.post("/api/budget/restart", json={"confirm": "RESTART"})).status_code == 409
        await finish(svc, run["run_id"])
        after = await c.post("/api/budget/restart", json={"confirm": "RESTART"})
        assert after.status_code == 200 and after.json()["spent_micros"] == 0
        spend = (await c.get("/api/spend")).json()["record"]
    periods = spend["periods"]
    assert len(periods) == 2 and periods[0]["id"] == first_period
    assert periods[0]["closed_total_micros"] >= 1_230_000 and periods[1]["ended_at"] is None
    assert any(e["label"] == "a probe" for e in spend["entries"])  # history is kept
    await svc.aclose()


async def test_the_spend_record_survives_a_restart_of_the_process(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    a = build_app_test_service(tmp_path, inputs_dir=inputs)
    assert a.record is not None
    eid = a.record.start_entry("run", run_id="rrn_x", label="x")
    a.record.add_cost(eid, "extract", 2500)
    await a.aclose()
    b = build_app_test_service(tmp_path, inputs_dir=inputs)
    assert b.record is not None and b.record.period_total_micros() == 2500
    await b.aclose()


# ----------------------------------------------------------------------------------------------- recovery
async def test_a_run_cut_off_by_a_restart_becomes_interrupted_with_a_terminal_event(
    tmp_path: Path,
) -> None:
    svc = build_app_test_service(tmp_path)
    row = RunRow(
        run_id="rrn_orphan",
        tenant_id="t1",
        program_id="p1",
        status="running",
        brief_hash="h",
        created_at=datetime.now(UTC),
    )
    await svc.store.create_run(row)
    await svc.store.append_event(
        Event(
            id="evt_1",
            seq=1,
            ts=datetime.now(UTC),
            run_id="rrn_orphan",
            type="run.started",
            label="started",
            counters=Counters(),
        )
    )
    assert await svc.recover() == 1
    got = await svc.store.get_run("t1", "rrn_orphan")
    assert got is not None and got.status == "interrupted"
    events = await svc.store.events_after("rrn_orphan", 0)
    assert [e.type for e in events] == ["run.started", "run.failed"]
    assert events[-1].detail["code"] == "interrupted"
    await svc.aclose()


async def test_a_stalled_run_is_failed_so_its_input_is_free_again(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    svc.stale_after_s = 60
    old = datetime.now(UTC) - timedelta(minutes=20)
    await svc.store.create_run(
        RunRow(
            run_id="rrn_s",
            tenant_id="t1",
            program_id="p1",
            status="running",
            brief_hash="h",
            created_at=old,
        )
    )
    assert await svc.sweep_stale() == 1
    got = await svc.store.get_run("t1", "rrn_s")
    assert got is not None and got.status == "failed"
    ev = await svc.store.events_after("rrn_s", 0)
    assert ev[-1].type == "run.failed" and ev[-1].detail["code"] == "stalled"
    await svc.aclose()


# ----------------------------------------------------------------------------------------------- security
async def test_a_run_cannot_be_started_from_another_origin(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    put(inputs, "yoga.json")
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    async with client(make_app(svc, tmp_path)) as c:
        iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
        r = await c.post(
            "/api/runs", json={"input_id": iid}, headers={"Origin": "https://evil.example"}
        )
        assert r.status_code == 403 and r.json()["code"] == "bad_origin"
        assert await svc.store.active_runs() == []
    await svc.aclose()


async def test_session_gives_the_page_its_token_and_the_front_end_is_served(tmp_path: Path) -> None:
    svc = build_app_test_service(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(svc, tmp_path)), base_url="http://test"
    ) as c:
        s = (await c.get("/api/session")).json()
        assert s["token"] == "dev-token" and s["app"] == "Research"
        assert (await c.get("/api/inputs")).status_code == 401  # the token is still required
        page = await c.get("/")
    assert (
        page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    )
    await svc.aclose()


def test_the_app_composition_cannot_reach_a_test_double() -> None:
    import ast

    src = Path(__file__).resolve().parents[2] / "src" / "mde_research_agent"
    banned = (
        "support",
        "fakes",
        "testing",
        "memory_store",
        "InMemoryStore",
        "FakeWorld",
        "devtools",
    )
    for name in ("compose.py", "api/app.py", "api/app_routes.py", "service.py", "__main__.py"):
        tree = ast.parse((src / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or "", *(a.name for a in node.names)]
            assert not [
                m
                for m in mods
                if any(b in m.split(".")[-1] or m.startswith("support") for b in banned)
            ], (
                name,
                mods,
            )
    # the app refuses to run on a double: every start needs a gateway and the live flags
    svc_src = (src / "service.py").read_text(encoding="utf-8")
    assert "NotReady" in svc_src
