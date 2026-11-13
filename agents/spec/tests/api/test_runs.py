"""Runs: start, snapshot, history, single-flight, stop and resume, and the two gates."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mde_spec_agent.api import create_app
from mde_spec_agent.service import SpecService

Snap = dict[str, Any]
YOGA = "quick-yoga-studio-bengaluru"
NASHIK = "standard-produce-marketplace-nashik"
FREIGHT = "thorough-freight-fleet-india"


def events(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    """The stored events of a run (a run waiting at a gate keeps its SSE stream open, which a test client cannot
    read to the end; the stream itself is tested in test_sse.py on finished runs)."""
    return [e.model_dump() for e in client.app.state.service.store.events(run_id)]  # type: ignore[attr-defined]


def test_start_run_then_snapshot_and_history(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    res = client.post("/api/runs", json={"compass_id": YOGA, "offline": True})
    assert res.status_code == 202 and set(res.json()) == {"run_id"}
    snap = start(YOGA)  # joins the same run (same input)
    assert snap["id"] == res.json()["run_id"] and snap["status"] == "awaiting_g2"
    assert snap["gates"] == {"g15": "closed", "g2": "open"}
    for key in (
        "step",
        "profile",
        "spend_micros",
        "estimate",
        "banners",
        "checked_by",
        "critic_vendor",
        "last_seq",
        "partial_reason",
        "content_hash",
    ):
        assert key in snap
    assert snap["last_seq"] >= 5 and len(snap["content_hash"]) == 64
    runs = client.get("/api/runs").json()["runs"]
    assert [r["id"] for r in runs] == [snap["id"]]
    assert {"id", "status", "profile", "spend_micros", "created_at", "pack_hash"} <= set(runs[0])


def test_unknown_input_and_profile_are_refused_plainly(client: TestClient) -> None:
    res = client.post("/api/runs", json={"compass_id": "no-such-export", "offline": True})
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    res = client.post("/api/runs", json={"compass_id": "synth:nope", "offline": True})
    assert res.status_code == 400 and "synthetic" in res.json()["error"]["message"]
    res = client.post(
        "/api/runs",
        json={"compass_id": YOGA, "offline": True, "consumer_profile": "ghost"},
    )
    assert res.status_code == 400 and res.json()["error"]["fix"]
    res = client.post(
        "/api/runs",
        json={"compass_id": YOGA, "research_id": "rrn_nothing", "offline": True},
    )
    assert res.status_code == 404 and "Research" in res.json()["error"]["message"]
    res = client.post(
        "/api/runs", json={"compass_id": "synth:tiny", "research_id": "rrn_x", "offline": True}
    )
    assert res.status_code == 400 and "synthetic" in res.json()["error"]["message"].lower()


def test_single_flight_same_input_joins_a_different_one_is_refused(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    first = start(YOGA)
    again = client.post("/api/runs", json={"compass_id": YOGA, "offline": True})
    assert again.status_code == 200
    assert again.json() == {"run_id": first["id"], "joined": True}
    other = client.post(
        "/api/runs",
        json={"compass_id": YOGA, "offline": True, "consumer_profile": "standard-default"},
    )
    assert other.status_code == 409
    err = other.json()["error"]
    assert err["code"] == "run_active" and err["run_id"] == first["id"] and err["fix"]


def test_events_are_dense_from_one_and_end_with_the_gate(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = start(YOGA)
    evs = events(client, snap["id"])
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert evs[0]["type"] == "run.queued" and evs[-1]["type"] == "gate.opened"
    assert evs[-1]["data"]["gate"] == "g2"
    assert all(e["run_id"] == snap["id"] and e["at"].endswith("+00:00") for e in evs)


def test_g15_pauses_deep_apps_and_records_the_consent(
    client: TestClient, start: Callable[..., Snap], wait: Callable[..., Snap]
) -> None:
    snap = start(FREIGHT)
    rid = snap["id"]
    assert snap["status"] == "paused_g15" and snap["gates"]["g15"] == "open"
    assert snap["profile"] == "deep"
    bad = client.post(f"/api/runs/{rid}/g15", json={"decision": "maybe"})
    assert bad.status_code == 400
    wrong = client.post(f"/api/runs/{rid}/g2", json={"decision": "approve", "content_hash": "x"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "gate_required"
    moves = [{"item": "FEAT-001", "to": "phase_2"}]
    changed = client.post(f"/api/runs/{rid}/g15", json={"decision": "change", "moves": moves})
    assert changed.status_code == 200 and changed.json()["gate"] == "open"
    assert client.get(f"/api/runs/{rid}").json()["status"] == "paused_g15"
    ok = client.post(
        f"/api/runs/{rid}/g15",
        json={"decision": "approve", "moves": moves, "everything_in_one_release": False},
    )
    assert ok.status_code == 200 and ok.json()["gate"] == "closed"
    done = wait(client, rid, ("awaiting_g2", "failed", "partial"))
    assert done["status"] == "awaiting_g2"
    record = done["gate_records"]["g15"]
    assert record["decision"] == "approved" and record["moves"] == moves and record["at"]
    types = [e["type"] for e in events(client, rid)]
    assert types.count("gate.opened") == 2 and "gate.closed" in types
    # a second approval of a gate that is already closed is a conflict
    assert client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"}).status_code == 409


def test_g15_everything_in_one_release_is_part_of_the_record(
    client: TestClient, start: Callable[..., Snap], wait: Callable[..., Snap]
) -> None:
    rid = start(FREIGHT)["id"]
    client.post(
        f"/api/runs/{rid}/g15", json={"decision": "approve", "everything_in_one_release": True}
    )
    done = wait(client, rid, ("awaiting_g2", "failed", "partial"))
    assert done["status"] == "awaiting_g2"
    assert done["gate_records"]["g15"]["everything_in_one_release"] is True


def test_g2_binds_the_content_hash_and_finishes_the_run(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = start(YOGA)
    rid, h = snap["id"], snap["content_hash"]
    stale = client.post(
        f"/api/runs/{rid}/g2", json={"decision": "approve", "content_hash": "0" * 64}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict"
    missing = client.post(f"/api/runs/{rid}/g2", json={"decision": "approve"})
    assert missing.status_code == 409
    assert (
        client.post(
            f"/api/runs/{rid}/g2", json={"decision": "reject", "content_hash": h}
        ).status_code
        == 400
    )
    ok = client.post(
        f"/api/runs/{rid}/g2",
        json={"decision": "approve", "content_hash": h, "suggestion_choices": {"s1": True}},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    after = client.get(f"/api/runs/{rid}").json()
    assert after["status"] == "approved" and after["approved_hash"] == h
    assert after["gate_records"]["g2"]["content_hash"] == h
    assert after["gate_records"]["g2"]["suggestion_choices"] == {"s1": True}
    evs = events(client, rid)
    assert evs[-1]["type"] == "run.finished" and evs[-1]["data"]["status"] == "approved"
    assert (
        client.post(
            f"/api/runs/{rid}/g2", json={"decision": "approve", "content_hash": h}
        ).status_code
        == 409
    )
    # the program is free again: the same input starts a NEW run
    again = client.post("/api/runs", json={"compass_id": YOGA, "offline": True})
    assert again.status_code == 202 and again.json()["run_id"] != rid


def test_g2_is_refused_while_blocking_needs_exist(
    service: SpecService,
    client: TestClient,
    start: Callable[..., Snap],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = start(YOGA)
    monkeypatch.setattr(service, "missing_needs", lambda run_id: ["BN-03"])
    res = client.post(
        f"/api/runs/{snap['id']}/g2",
        json={"decision": "approve", "content_hash": snap["content_hash"]},
    )
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "gate_required" and err["blocking_needs"] == ["BN-03"] and err["fix"]
    assert client.get(f"/api/runs/{snap['id']}").json()["status"] == "awaiting_g2"


def test_stop_then_resume_ends_with_the_same_bytes_as_an_uninterrupted_run(
    make_paced: Callable[..., Any],
    make_service: Callable[..., SpecService],
    wait: Callable[..., Snap],
) -> None:
    clean_svc = make_service()
    with TestClient(create_app(clean_svc, settings=clean_svc.settings)) as c:
        rid = c.post("/api/runs", json={"compass_id": YOGA, "offline": True}).json()["run_id"]
        clean = wait(c, rid, ("awaiting_g2",))
    svc = make_paced("paced")
    with TestClient(create_app(svc, settings=svc.settings)) as c:
        rid = c.post("/api/runs", json={"compass_id": YOGA, "offline": True}).json()["run_id"]
        time.sleep(0.4)
        res = c.post(f"/api/runs/{rid}/stop")
        assert res.status_code == 200
        snap = wait(c, rid, ("partial", "awaiting_g2"))
        assert snap["status"] == "partial" and snap["partial_reason"] == "stop"
        evs = events(c, rid)
        assert evs[-1]["type"] == "run.partial" and evs[-1]["data"]["reason"] == "stop"
        assert c.post(f"/api/runs/{rid}/stop").status_code == 409  # nothing active to stop
        svc.pace = 0.0
        assert c.post(f"/api/runs/{rid}/resume").status_code == 202
        done = wait(c, rid, ("awaiting_g2", "failed"))
        assert done["status"] == "awaiting_g2"
        assert done["content_hash"] == clean["content_hash"]
        assert done["pack_hash"] == clean["pack_hash"]
        types = [e["type"] for e in events(c, rid)]
        assert "run.resumed" in types
        assert [e["seq"] for e in events(c, rid)] == list(range(1, len(types) + 1))


def test_resume_only_for_partial_runs(client: TestClient, start: Callable[..., Snap]) -> None:
    snap = start(YOGA)
    res = client.post(f"/api/runs/{snap['id']}/resume")
    assert res.status_code == 409 and res.json()["error"]["code"] == "conflict"


def test_stop_all_and_shutdown_stop_active_runs_gracefully(
    make_paced: Callable[..., Any],
) -> None:
    svc = make_paced("paced2")
    with TestClient(create_app(svc, settings=svc.settings)) as c:
        rid = c.post("/api/runs", json={"compass_id": YOGA, "offline": True}).json()["run_id"]
        time.sleep(0.3)
        res = c.post("/api/stop")
        assert res.status_code == 200 and res.json()["stopped"] == 1
        deadline = time.monotonic() + 20
        while (
            time.monotonic() < deadline and c.get(f"/api/runs/{rid}").json()["status"] != "partial"
        ):
            time.sleep(0.05)
        assert c.get(f"/api/runs/{rid}").json()["partial_reason"] == "stop"
        # a second run left running when the app shuts down (the SIGTERM path) is stopped, not abandoned
        svc.pace = 0.4
        rid2 = c.post("/api/runs", json={"compass_id": NASHIK, "offline": True}).json()["run_id"]
        time.sleep(0.3)
    final = svc.store.get_run(rid2)
    assert final["status"] == "partial" and final["partial_reason"] == "stop"


def test_a_restart_marks_orphaned_runs_partial_with_a_terminal_event(
    service: SpecService, make_service: Callable[..., SpecService]
) -> None:
    j = service.store.create_run("prog-x", "h", {"offline": True})
    service.store.update_run(j.run_id, status="running")
    fresh = make_service()
    assert fresh.recover() == [j.run_id]
    rec = fresh.store.get_run(j.run_id)
    assert rec["status"] == "partial" and rec["partial_reason"] == "crash"
    assert fresh.store.events(j.run_id)[-1].type == "run.partial"
