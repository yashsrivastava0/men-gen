"""The pack endpoints: documented shapes, the manifest-validated file route, no traversal, the diff of two runs."""

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


def test_pack_endpoints_before_the_pack_exists_are_a_plain_conflict(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = start(FREIGHT)
    assert snap["status"] == "paused_g15"  # the Freeze is done, assemble is not
    res = client.get(f"/api/runs/{snap['id']}/pack/tree")
    assert res.status_code == 409 and res.json()["error"]["code"] == "conflict"


def test_tree_tasks_budget_manifest(client: TestClient, run: Snap) -> None:
    base = f"/api/runs/{run['id']}/pack"
    tree = client.get(f"{base}/tree").json()
    assert tree["task_read_budget"] == 28000
    assert any(f["path"] == "prefix.bundle.md" and f["tokens"] > 0 for f in tree["files"])
    assert tree["tasks"] and {"id", "title", "read_tokens", "budget"} <= set(tree["tasks"][0])
    assert all(t["read_tokens"] <= t["budget"] for t in tree["tasks"])
    tasks = client.get(f"{base}/tasks").json()
    assert tasks["waves"] and {"id", "title", "tokens"} <= set(tasks["waves"][0]["tasks"][0])
    assert sum(len(w["tasks"]) for w in tasks["waves"]) == len(tree["tasks"])
    budget = client.get(f"{base}/budget").json()
    assert budget["tasks_over_budget"] == [] and budget["total_tokens"] > 0
    assert "screens" in budget["tokens_by_kind"] and ".md" in budget["tokens_by_format"]
    manifest = client.get(f"{base}/manifest").json()
    assert (
        manifest["schema"] == "spec.manifest.v1" and manifest["content_hash"] == run["content_hash"]
    )
    assert manifest["checked_by"] == ["self_check"]


def test_checks_selfcheck_verify(client: TestClient, run: Snap) -> None:
    base = f"/api/runs/{run['id']}/pack"
    checks = client.get(f"{base}/checks").json()
    assert isinstance(checks["results"], list) and "coverage" in checks
    assert isinstance(checks["builder_needs"], list)
    for r in checks["results"]:
        assert {"code", "severity", "where", "why", "fix"} <= set(r)
    selfcheck = client.get(f"{base}/selfcheck").json()
    assert selfcheck["schema"] and "rcode" in selfcheck
    assert client.get(f"{base}/verify").json() == {"enabled": False}  # a small app has no Verify


def test_deep_pack_has_verify_evidence_and_says_what_it_did(
    client: TestClient, start: Callable[..., Snap], wait: Callable[..., Snap]
) -> None:
    rid = start(FREIGHT)["id"]
    client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
    snap = wait(client, rid, ("awaiting_g2", "failed"))
    assert snap["status"] == "awaiting_g2"
    assert snap["checked_by"] == ["self_check", "verify"] and snap["critic_vendor"] in (
        "same",
        "different",
    )
    ev = client.get(f"/api/runs/{rid}/pack/verify").json()
    assert ev["enabled"] is True and ev["verify"] == "ran" and ev["canary_summary"]["cards"] >= 1
    assert (
        ev["patches_applied"] == 0
    )  # proposed only: spec-core has no applier, and the file says so
    manifest = client.get(f"/api/runs/{rid}/pack/manifest").json()
    assert (
        manifest["checked_by"] == ["self_check", "verify"]
        and manifest["critic_vendor"] == snap["critic_vendor"]
    )


def test_file_route_serves_plain_text_validated_against_the_manifest(
    client: TestClient, run: Snap
) -> None:
    res = client.get(f"/api/runs/{run['id']}/pack/file", params={"path": "prefix.bundle.md"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "AGENTS" in res.text or len(res.text) > 100
    ev = client.get(f"/api/runs/{run['id']}/pack/file", params={"path": "evidence/self-check.json"})
    assert ev.status_code == 200 and ev.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "path",
    [
        "../run.json",
        "../../.store.lock",
        "/etc/passwd",
        "screens/../../run.json",
        "pack/MANIFEST.json",  # not a pack-relative path
        "screens\\SCR-001.md",
        "screens/NOPE.md",
        "",
        "evidence/../run.json",
        "..%2Frun.json",
    ],
)
def test_file_route_refuses_anything_that_is_not_a_pack_file(
    client: TestClient, run: Snap, path: str
) -> None:
    res = client.get(f"/api/runs/{run['id']}/pack/file", params={"path": path})
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    assert "events_" not in res.text and "input_hash" not in res.text


def test_file_route_ignores_a_stray_file_not_in_the_manifest(
    service: Any, client: TestClient, run: Snap
) -> None:
    stray = service.store.run_dir(run["id"]) / "pack" / "stray.md"
    stray.write_text("planted", encoding="utf-8")
    assert (
        client.get(f"/api/runs/{run['id']}/pack/file", params={"path": "stray.md"}).status_code
        == 404
    )


def test_diff_of_two_packs_and_of_a_pack_with_itself(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    a = start(YOGA)
    client.post(
        f"/api/runs/{a['id']}/g2", json={"decision": "approve", "content_hash": a["content_hash"]}
    )
    b = start(NASHIK)
    d = client.get(f"/api/runs/{a['id']}/diff/{b['id']}").json()
    assert d["identical"] is False and d["counts"]["added"] + d["counts"]["changed"] > 0
    assert {f["status"] for f in d["files"]} <= {"added", "removed", "changed", "same"}
    same = client.get(f"/api/runs/{a['id']}/diff/{a['id']}").json()
    assert same["identical"] is True and same["counts"]["same"] > 0
    assert client.get(f"/api/runs/{a['id']}/diff/run_NOPE00000000000000000000").status_code == 404
