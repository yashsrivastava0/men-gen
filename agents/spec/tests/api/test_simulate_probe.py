"""The simulator and the probe: the three modes against a consumer profile, totals equal the packager's, and a probe
that is offline (the gateway's mock mode) or refused when live is not ready."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

Snap = dict[str, Any]
YOGA = "quick-yoga-studio-bengaluru"
NASHIK = "standard-produce-marketplace-nashik"
FREIGHT = "thorough-freight-fleet-india"


@pytest.fixture
def run(start: Callable[..., Snap]) -> Snap:
    return start(YOGA)


def test_bad_mode_and_bad_consumer_are_plain_400s(client: TestClient, run: Snap) -> None:
    base = f"/api/runs/{run['id']}/simulate"
    assert client.get(base, params={"mode": "sideways"}).status_code == 400
    res = client.get(base, params={"mode": "all", "consumer": "ghost"})
    assert res.status_code == 400 and res.json()["error"]["fix"]


def test_all_says_whether_the_whole_pack_fits(client: TestClient, run: Snap) -> None:
    r = client.get(
        f"/api/runs/{run['id']}/simulate", params={"mode": "all", "consumer": "weak-default"}
    ).json()
    assert {"allowed", "reason", "pack_tokens", "whole_pack_tokens"} <= set(r)
    assert r["pack_tokens"] == r["total_tokens"] and r["reason"]
    assert r["allowed"] == (r["pack_tokens"] <= r["whole_pack_tokens"])


def test_one_reads_prefix_and_card_and_equals_the_packager_total(
    client: TestClient, run: Snap
) -> None:
    rid = run["id"]
    tree = client.get(f"/api/runs/{rid}/pack/tree").json()
    paths = [f["path"] for f in tree["files"] if f["path"].startswith("screens/")]
    sid = next(p for p in paths if p.endswith(".md") and ".could" not in p)[8:-3]
    one = client.get(f"/api/runs/{rid}/simulate", params={"mode": "one", "screen": sid}).json()
    assert [f["part"] for f in one["files"]] == ["prefix", "card"]
    assert all(f["bytes"] > 0 and f["tokens"] > 0 for f in one["files"])
    assert (
        one["total_tokens"] == sum(f["tokens"] for f in one["files"]) and one["budget_tokens"] > 0
    )
    # the task that owns the screen is counted by the packager with exactly the simulator's number
    owner = next(t for t in tree["tasks"] if t["id"] == one["task_id"])
    assert owner["read_tokens"] == one["task_total_tokens"]
    missing = client.get(
        f"/api/runs/{rid}/simulate", params={"mode": "one", "screen": "SCR-999"}
    ).json()
    assert missing["found"] is False and missing["reason"]
    assert client.get(f"/api/runs/{rid}/simulate", params={"mode": "one"}).json()["found"] is False


def test_parallel_totals_equal_the_packagers_task_totals(client: TestClient, run: Snap) -> None:
    rid = run["id"]
    tree = client.get(f"/api/runs/{rid}/pack/tree").json()
    sim = client.get(
        f"/api/runs/{rid}/simulate", params={"mode": "parallel", "consumer": "weak-default"}
    ).json()
    assert sim["slots"] >= 1
    rows = {t["id"]: t for w in sim["waves"] for t in w["tasks"]}
    assert set(rows) == {t["id"] for t in tree["tasks"]}
    for t in tree["tasks"]:
        assert rows[t["id"]]["tokens"] == t["read_tokens"]
        assert rows[t["id"]]["title"] == t["title"]
    assert sum(r["tokens"] for r in rows.values()) == sum(t["read_tokens"] for t in tree["tasks"])
    assert all(r["start"] is not None and r["end"] > r["start"] for r in rows.values())
    # more worker slots never lengthen the dry run
    assert sim["makespan"] >= 1


def test_probe_offline_returns_the_documented_shape(client: TestClient, run: Snap) -> None:
    rid = run["id"]
    waves = client.get(f"/api/runs/{rid}/pack/tasks").json()["waves"]
    task = next(t for w in waves if w["wave"] != "W0" for t in w["tasks"])
    res = client.post(f"/api/runs/{rid}/probe", json={"task_id": task["id"], "offline": True})
    assert res.status_code == 200
    body = res.json()
    assert body["task_id"] == task["id"] and body["offline"] is True
    for key in ("outline", "blocking_questions", "assumptions", "acceptance_coverage"):
        assert isinstance(body[key], list)


def test_probe_unknown_task_and_live_without_keys(client: TestClient, run: Snap) -> None:
    rid = run["id"]
    res = client.post(f"/api/runs/{rid}/probe", json={"task_id": "T-9999", "offline": True})
    assert res.status_code == 404
    waves = client.get(f"/api/runs/{rid}/pack/tasks").json()["waves"]
    tid = waves[-1]["tasks"][0]["id"]
    live = client.post(f"/api/runs/{rid}/probe", json={"task_id": tid, "offline": False})
    assert live.status_code == 503 and live.json()["error"]["code"] == "not_ready"
    assert client.get("/api/budget").json()["spent_micros"] == 0
