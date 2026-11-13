"""The view routes: the documented shapes (from the real view functions), plain 404 and 400 for a bad name, and the
exported ``preview.html`` as a download."""

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
def yoga(start: Callable[..., Snap]) -> Snap:
    return start("quick-yoga-studio-bengaluru")


def first_screen_and_flow(service: Any, run_id: str) -> tuple[str, str]:
    g = service.load(run_id).graph
    sid = next(s for s, n in sorted(g.screens.items()) if n.status == "in")
    fid = next(iter(sorted(g.flows)), "")
    return sid, fid


def test_g15_view_shape(client: TestClient, yoga: Snap) -> None:
    v = client.get(f"/api/runs/{yoga['id']}/views/g15").json()
    for key in ("app_map", "roles", "entities", "inventory", "phases"):
        assert key in v and isinstance(v[key], list)


def test_g2_view_shape_and_it_carries_the_hash_to_approve(client: TestClient, yoga: Snap) -> None:
    v = client.get(f"/api/runs/{yoga['id']}/views/g2").json()
    assert v["content_hash"] == yoga["content_hash"]
    for key in ("summary", "flows", "confirm_list", "assumptions", "risks", "content_maps"):
        assert key in v
    assert v["summary"]["text"]
    # the views stay readable after approval (the hand-off views use them)
    client.post(
        f"/api/runs/{yoga['id']}/g2",
        json={"decision": "approve", "content_hash": v["content_hash"]},
    )
    assert client.get(f"/api/runs/{yoga['id']}/views/g2").status_code == 200


def test_screen_journey_graph_data_needs(service: Any, client: TestClient, yoga: Snap) -> None:
    rid = yoga["id"]
    sid, fid = first_screen_and_flow(service, rid)
    screen = client.get(f"/api/runs/{rid}/views/screen/{sid}").json()
    assert screen["id"] == sid and "regions" in screen and "actions" in screen
    assert (
        client.get(f"/api/runs/{rid}/views/screen/{sid}", params={"profile": "typical"}).status_code
        == 200
    )
    if fid:
        j = client.get(f"/api/runs/{rid}/views/journey/{fid}").json()
        assert "steps" in j and "stories" in j
    graph = client.get(f"/api/runs/{rid}/views/graph").json()
    assert graph["nodes"] and "edges" in graph and "unreachable" in graph
    data = client.get(f"/api/runs/{rid}/views/data").json()
    assert "entities" in data and "integrations" in data
    needs = client.get(f"/api/runs/{rid}/views/needs").json()
    assert {"needs", "blocks"} <= set(needs)


def test_unknown_screen_flow_and_run_are_plain_404s(client: TestClient, yoga: Snap) -> None:
    rid = yoga["id"]
    for path in ("views/screen/SCR-NOPE", "views/journey/FLOW-NOPE"):
        res = client.get(f"/api/runs/{rid}/{path}")
        assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    assert client.get("/api/runs/run_NOPE00000000000000000000/views/g15").status_code == 404


def test_g15_view_works_while_paused_but_g2_needs_the_pack(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = start(FREIGHT)
    assert snap["status"] == "paused_g15"
    assert client.get(f"/api/runs/{snap['id']}/views/g15").status_code == 200
    res = client.get(f"/api/runs/{snap['id']}/views/g2")
    assert res.status_code == 409 and res.json()["error"]["code"] == "conflict"


def test_export_preview_html_is_one_downloadable_page(client: TestClient, yoga: Snap) -> None:
    res = client.get(f"/api/runs/{yoga['id']}/export/preview.html")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    assert 'attachment; filename="preview.html"' in res.headers["content-disposition"]
    assert res.text.startswith("<!doctype html>") and "<script" not in res.text.lower()
    assert yoga["content_hash"] in res.text
