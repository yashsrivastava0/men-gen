"""The owner's journey through the API, offline: inputs, auto-select, preview, start, (G1.5), run, G2, approve, the pack,
the three simulator modes, the export. The simulator totals must equal the packager's task totals and the approved hash
must bind."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from mde_compass_intake import intake as read_intake

INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"
Snap = dict[str, Any]
Maker = Callable[..., Any]


def stored_events(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    return [e.model_dump() for e in client.app.state.service.store.events(run_id)]  # type: ignore[attr-defined]


def check_simulator_matches_packager(client: TestClient, rid: str) -> None:
    tree = client.get(f"/api/runs/{rid}/pack/tree").json()
    packager = {t["id"]: t["read_tokens"] for t in tree["tasks"]}
    assert packager and all(v > 0 for v in packager.values())

    # parallel: every task card with the packager's total, and the worker slots come from the profile
    par = client.get(
        f"/api/runs/{rid}/simulate", params={"mode": "parallel", "consumer": "weak-default"}
    ).json()
    sim = {t["id"]: t["tokens"] for w in par["waves"] for t in w["tasks"]}
    assert sim == packager
    profiles = {p["id"]: p for p in client.get("/api/profiles").json()["profiles"]}
    assert par["slots"] == profiles["weak-default"]["k"]

    # one: the screen's prefix + card; the task that owns it is counted exactly like the packager counts it
    screens = [
        f["path"][8:-3]
        for f in tree["files"]
        if f["path"].startswith("screens/")
        and f["path"].endswith(".md")
        and ".could" not in f["path"]
    ]
    one = client.get(
        f"/api/runs/{rid}/simulate", params={"mode": "one", "screen": screens[0]}
    ).json()
    assert one["total_tokens"] == sum(f["tokens"] for f in one["files"])
    assert one["task_total_tokens"] == packager[one["task_id"]]

    # all: allowed exactly when the whole pack fits, and the reason says it
    whole = client.get(f"/api/runs/{rid}/simulate", params={"mode": "all"}).json()
    assert (
        whole["allowed"] == (whole["pack_tokens"] <= whole["whole_pack_tokens"]) and whole["reason"]
    )


def test_quick_yoga_export_from_inputs_to_a_bound_approval(
    make_client: Maker, waiter: Callable[..., Snap]
) -> None:
    with make_client() as client:
        # step 1: inputs and auto-select (the same function the terminal uses)
        inputs = client.get("/api/inputs").json()
        assert "quick-yoga-studio-bengaluru" in {e["id"] for e in inputs["compass"]}
        auto = client.get("/api/inputs/auto", params={"size": "compact"}).json()
        assert auto["compass_id"]
        compass_id = "quick-yoga-studio-bengaluru"

        # step 2: start; the zero-cost preview is readable on the run and costs nothing
        started = client.post("/api/runs", json={"compass_id": compass_id, "offline": True})
        assert started.status_code == 202
        rid = started.json()["run_id"]
        pv = client.get(f"/api/runs/{rid}/preview").json()
        assert pv["profile"]["id"] == "compact" and pv["inventory_count"] > 0
        assert pv["estimate"]["high_micros"] >= pv["estimate"]["low_micros"] > 0

        # a compact app has no G1.5: the run goes straight to G2
        snap = waiter(client, rid, ("awaiting_g2", "paused_g15", "failed", "partial"))
        assert snap["status"] == "awaiting_g2", snap.get("note")
        assert snap["gates"] == {"g15": "closed", "g2": "open"} and snap["spend_micros"] == 0
        assert client.get(f"/api/runs/{rid}/pack/verify").json() == {"enabled": False}

        # G2: the page the owner reads, then approval bound to the hash on that page
        g2 = client.get(f"/api/runs/{rid}/views/g2").json()
        assert g2["content_hash"] == snap["content_hash"] and g2["summary"]["text"]
        before = client.post(
            f"/api/runs/{rid}/g2", json={"decision": "approve", "content_hash": "f" * 64}
        )
        assert before.status_code == 409  # the wrong hash binds nothing
        done = client.post(
            f"/api/runs/{rid}/g2", json={"decision": "approve", "content_hash": g2["content_hash"]}
        )
        assert done.status_code == 200
        final = client.get(f"/api/runs/{rid}").json()
        assert (
            final["status"] == "approved"
            and final["approved_hash"] == final["content_hash"] == g2["content_hash"]
        )
        manifest = client.get(f"/api/runs/{rid}/pack/manifest").json()
        assert manifest["content_hash"] == final["content_hash"]
        assert stored_events(client, rid)[-1]["type"] == "run.finished"

        # the hand-off: the pack, the simulator and the export
        check_simulator_matches_packager(client, rid)
        html = client.get(f"/api/runs/{rid}/export/preview.html")
        assert html.status_code == 200 and html.text.startswith("<!doctype html>")
        assert "<script" not in html.text.lower()
        assert client.get("/api/budget").json()["spent_micros"] == 0


def test_thorough_export_pauses_at_g15_and_dispatch_is_in_phase_1(
    make_client: Maker, waiter: Callable[..., Snap]
) -> None:
    with make_client() as client:
        rid = client.post(
            "/api/runs", json={"compass_id": "thorough-freight-fleet-india", "offline": True}
        ).json()["run_id"]
        snap = waiter(client, rid, ("paused_g15", "awaiting_g2", "failed", "partial"))
        assert (
            snap["status"] == "paused_g15"
            and snap["profile"] == "deep"
            and snap["gates"]["g15"] == "open"
        )
        assert snap["checked_by"] == ["self_check"]  # nothing has been verified yet

        g15 = client.get(f"/api/runs/{rid}/views/g15").json()
        assert [p["name"] for p in g15["phases"]][:2] == [
            "Phase 1 (first release)",
            "Phase 2 (later)",
        ]
        dispatch = [i for i in g15["inventory"] if i["module"] == "Dispatch"]
        assert dispatch, "the structure view lists the Dispatch module"
        assert any(i["phase"].startswith("Phase 1") for i in dispatch), "Dispatch is in Phase 1"

        # the consent is recorded, then the run continues, Verify included
        ok = client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
        assert ok.status_code == 200
        done = waiter(client, rid, ("awaiting_g2", "failed", "partial"))
        assert done["status"] == "awaiting_g2", done.get("note")
        assert done["gate_records"]["g15"]["decision"] == "approved"
        assert done["checked_by"] == ["self_check", "verify"]
        verify = client.get(f"/api/runs/{rid}/pack/verify").json()
        assert verify["enabled"] is True and verify["canary_summary"]["cards"] > 0
        g2 = client.get(f"/api/runs/{rid}/views/g2").json()
        assert (
            client.post(
                f"/api/runs/{rid}/g2",
                json={"decision": "approve", "content_hash": g2["content_hash"]},
            ).status_code
            == 200
        )
        check_simulator_matches_packager(client, rid)


def test_synthetic_preset_goes_through_the_same_flow(
    make_client: Maker, waiter: Callable[..., Snap]
) -> None:
    with make_client() as client:
        rid = client.post("/api/runs", json={"compass_id": "synth:small", "offline": True}).json()[
            "run_id"
        ]
        snap = waiter(client, rid, ("awaiting_g2", "paused_g15", "failed", "partial"))
        assert snap["status"] == "awaiting_g2", snap.get("note")
        assert (
            client.post(
                f"/api/runs/{rid}/g2",
                json={"decision": "approve", "content_hash": snap["content_hash"]},
            ).status_code
            == 200
        )
        check_simulator_matches_packager(client, rid)


def test_a_poisoned_quote_never_becomes_markup(
    make_client: Maker, waiter: Callable[..., Snap], tmp_path: Path
) -> None:
    export = json.loads((INPUTS / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8"))
    spec_input = read_intake(export).spec_input
    assert spec_input is not None
    name = spec_input.features[0].name
    poison = "<script>alert(1)</script> " + name
    text = json.dumps(export, ensure_ascii=False).replace(
        json.dumps(name, ensure_ascii=False), json.dumps(poison, ensure_ascii=False)
    )
    assert (
        poison in json.loads(text)["consumer_views"]["research"]["feature_intents"][0]["name"]
        or text.count("<script>") > 0
    )
    with make_client() as client:
        up = client.post(
            "/api/inputs/upload", content=text, headers={"content-type": "application/json"}
        )
        assert up.status_code == 201, up.text
        rid = client.post(
            "/api/runs", json={"compass_id": up.json()["id"], "offline": True}
        ).json()["run_id"]
        snap = waiter(client, rid, ("awaiting_g2", "paused_g15", "failed", "partial"))
        assert snap["status"] == "awaiting_g2", snap.get("note")
        html = client.get(f"/api/runs/{rid}/export/preview.html").text
        assert "<script>alert(1)</script>" not in html and "<script" not in html.lower()
        assert "&lt;script&gt;" in html  # the hostile name is on the page, as text
        # the pack files carry the text as data and are served as plain text, never as a page
        tree = client.get(f"/api/runs/{rid}/pack/tree").json()
        served = [
            client.get(f"/api/runs/{rid}/pack/file", params={"path": f["path"]})
            for f in tree["files"][:40]
        ]
        assert all(
            r.headers["content-type"].startswith("text/plain")
            and r.headers["x-content-type-options"] == "nosniff"
            for r in served
        )
