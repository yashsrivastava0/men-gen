"""Inputs: discovery of the real Compass exports, auto-select, upload."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"
YOGA = INPUTS / "quick-yoga-studio-bengaluru.json"


def test_inputs_lists_every_export_classified(client: TestClient) -> None:
    body = client.get("/api/inputs").json()
    by_id = {e["id"]: e for e in body["compass"]}
    yoga = by_id["quick-yoga-studio-bengaluru"]
    assert yoga["tier"] == "quick" and yoga["profile"] == "compact"
    assert yoga["readiness"] == {"ready": True, "missing": []}
    assert {"format", "size_band", "drift_flags", "research_matches", "path"} <= set(yoga)
    assert by_id["thorough-freight-fleet-india"]["profile"] == "deep"
    assert body["research"] == []  # the tests point at an empty Research folder
    assert "synth:big" in body["synthetic"]


def test_auto_proposes_one_pair_per_size(client: TestClient) -> None:
    for size in ("compact", "standard", "deep", "program"):
        p = client.get("/api/inputs/auto", params={"size": size}).json()
        assert (
            p["compass_id"] and p["reason"] and isinstance(p["gaps"], list) and "research_id" in p
        )
    deep = client.get("/api/inputs/auto", params={"size": "deep"}).json()
    assert deep["compass_id"] == "thorough-freight-fleet-india"
    assert client.get("/api/inputs/auto", params={"size": "enormous"}).status_code == 400


def test_upload_stores_the_export_under_a_safe_name(client: TestClient, service) -> None:  # type: ignore[no-untyped-def]
    raw = YOGA.read_text(encoding="utf-8")
    res = client.post(
        "/api/inputs/upload", content=raw, headers={"content-type": "application/json"}
    )
    assert res.status_code == 201
    entry = res.json()
    assert entry["id"].startswith("upload-") and entry["readiness"]["ready"] is True
    stored = Path(entry["path"])
    assert stored.parent == service.data / "inputs" and stored.is_file()
    assert entry["id"] in {e["id"] for e in client.get("/api/inputs").json()["compass"]}
    assert (
        client.post("/api/runs", json={"compass_id": entry["id"], "offline": True}).status_code
        == 202
    )


def test_upload_refuses_what_is_not_a_compass_export(client: TestClient) -> None:
    for raw in (b"not json", b"[1,2,3]", json.dumps({"hello": "world"}).encode(), b"\xff\xfe\x00"):
        res = client.post(
            "/api/inputs/upload", content=raw, headers={"content-type": "application/json"}
        )
        assert res.status_code == 400, raw
        assert res.json()["error"]["code"] == "bad_request"


def test_upload_is_size_limited(client: TestClient) -> None:
    big = b"{" + b" " * (9 * 1024 * 1024) + b"}"
    res = client.post(
        "/api/inputs/upload", content=big, headers={"content-type": "application/json"}
    )
    assert res.status_code == 413 and res.json()["error"]["code"] == "too_large"
