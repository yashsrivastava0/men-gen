"""G1.5 "change": the owner's moves between the first release and later are APPLIED (the release plan is derived again
and the run stops at this gate once more), recorded as the I-4 override, and carried through to the approved pack."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mde_spec_agent.derive.choices import moves_in_force
from mde_spec_agent.service import SpecService

FREIGHT = "thorough-freight-fleet-india"
Snap = dict[str, Any]


def _wait_paused(client: TestClient, rid: str, since_changes: int = 0) -> Snap:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        snap = client.get(f"/api/runs/{rid}").json()
        if snap["status"] in ("failed", "partial", "awaiting_g2"):
            return snap
        if snap["status"] == "paused_g15" and snap.get("last_seq", 0) > since_changes:
            return snap
        time.sleep(0.2)
    raise AssertionError("the run did not stop at G1.5")


@pytest.fixture
def paused(client: TestClient, wait: Callable[..., Snap]) -> tuple[str, Snap]:
    rid = client.post("/api/runs", json={"compass_id": FREIGHT, "offline": True}).json()["run_id"]
    snap = wait(client, rid, ("paused_g15", "failed", "partial"))
    assert snap["status"] == "paused_g15"
    return rid, snap


def _features(service: SpecService, rid: str) -> dict[str, Any]:
    return service.load(rid).graph.features


def test_moving_a_feature_to_later_is_applied_and_is_the_persons_own_deferral(
    service: SpecService,
    client: TestClient,
    paused: tuple[str, Snap],
    wait: Callable[..., Snap],
) -> None:
    rid, snap = paused
    feats = _features(service, rid)
    movable = sorted(f for f, v in feats.items() if v.status == "in")
    assert movable, "the Thorough export has features in the first release"
    fid = movable[0]
    before_seq = snap["last_seq"]
    res = client.post(
        f"/api/runs/{rid}/g15", json={"decision": "change", "moves": [{"item": fid, "to": "later"}]}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True and body["gate"] == "open" and body["moves"] == 1
    again = _wait_paused(client, rid, before_seq)
    assert again["status"] == "paused_g15"
    after = _features(service, rid)[fid]
    assert after.status == "later" and after.later_reason == "user"
    # the request is on file (I-4: who asked for what and when) and survives to the approval
    rec = service.get_record(rid)
    assert rec["g15_changes"][0]["moves"] == [{"item": fid, "to": "later", "asked": fid}]
    ok = client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
    assert ok.status_code == 200
    done = wait(client, rid, ("awaiting_g2", "failed", "partial"))
    assert done["status"] == "awaiting_g2"
    assert done["gate_records"]["g15"]["moves"] == [{"item": fid, "to": "later"}]
    assert _features(service, rid)[fid].status == "later"  # still later in the approved pack
    assert moves_in_force(service.get_record(rid)) == ((fid, "later"),)


def test_moving_a_later_feature_into_the_first_release_pulls_its_screens_in(
    service: SpecService,
    client: TestClient,
    paused: tuple[str, Snap],
) -> None:
    rid, snap = paused
    feats = _features(service, rid)
    screens_of: dict[str, int] = {}
    graph = service.load(rid).graph
    for s in graph.screens.values():
        for f in getattr(s, "feature_ids", []) or []:
            screens_of[f] = screens_of.get(f, 0) + 1
    later = sorted(f for f, v in feats.items() if v.status == "later" and v.later_reason != "user")
    assert later, "Thorough keeps first-release features as later"
    fid = later[0]
    before_screens = len(graph.screens)
    res = client.post(
        f"/api/runs/{rid}/g15", json={"decision": "change", "moves": [{"item": fid, "to": "in"}]}
    )
    assert res.status_code == 200
    again = _wait_paused(client, rid, snap["last_seq"])
    assert again["status"] == "paused_g15"
    assert _features(service, rid)[fid].status == "in"
    assert len(service.load(rid).graph.screens) >= before_screens


def test_a_move_the_run_cannot_make_is_refused_in_plain_words_and_nothing_changes(
    client: TestClient, paused: tuple[str, Snap]
) -> None:
    rid, snap = paused
    bad_item = client.post(
        f"/api/runs/{rid}/g15",
        json={"decision": "change", "moves": [{"item": "FCT-NOPE", "to": "later"}]},
    )
    assert bad_item.status_code == 400 and "FCT-NOPE" in bad_item.json()["error"]["message"]
    bad_to = client.post(
        f"/api/runs/{rid}/g15",
        json={"decision": "change", "moves": [{"item": "FCT-001", "to": "sideways"}]},
    )
    assert bad_to.status_code == 400 and bad_to.json()["error"]["fix"]
    empty = client.post(f"/api/runs/{rid}/g15", json={"decision": "change", "moves": []})
    assert empty.status_code == 400
    after = client.get(f"/api/runs/{rid}").json()
    assert after["status"] == "paused_g15" and after["last_seq"] == snap["last_seq"]


def test_moves_in_force_reads_the_gate_record_after_approval_and_the_requests_before() -> None:
    asked = {
        "g15_changes": [
            {"moves": [{"item": "F1", "to": "later"}]},
            {"moves": [{"item": "F2", "to": "in"}]},
        ]
    }
    assert moves_in_force(asked) == (("F1", "later"), ("F2", "in"))
    approved = {**asked, "gates": {"g15": {"moves": [{"item": "F9", "to": "in"}]}}}
    assert moves_in_force(approved) == (("F9", "in"),)
    assert moves_in_force({}) == ()
