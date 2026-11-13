"""Same origin only: foreign Origin refused, no CORS, strict CSP, body limits, the front end served at ``/``."""

from __future__ import annotations

from fastapi.testclient import TestClient

YOGA = "quick-yoga-studio-bengaluru"
BODY = {"compass_id": YOGA, "offline": True}


def test_a_foreign_origin_cannot_start_a_run(client: TestClient) -> None:
    res = client.post("/api/runs", json=BODY, headers={"Origin": "http://evil.example"})
    assert res.status_code == 403
    err = res.json()["error"]
    assert err["code"] == "bad_origin" and err["message"]
    assert client.get("/api/runs").json() == {"runs": []}  # nothing was started


def test_cross_site_fetch_metadata_is_refused_but_the_own_origin_passes(client: TestClient) -> None:
    assert (
        client.post("/api/runs", json=BODY, headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    own = client.post("/api/runs", json=BODY, headers={"Origin": "http://testserver"})
    assert own.status_code == 202
    assert client.post("/api/stop", headers={"Origin": "http://127.0.0.1:1"}).status_code == 403


def test_every_state_change_checks_the_origin(client: TestClient) -> None:
    for path in (
        "/api/stop",
        "/api/runs/run_X/stop",
        "/api/runs/run_X/resume",
        "/api/inputs/upload",
    ):
        res = client.post(path, content=b"{}", headers={"Origin": "http://evil.example"})
        assert res.status_code == 403 and res.json()["error"]["code"] == "bad_origin"


def test_reads_are_not_origin_checked(client: TestClient) -> None:
    assert (
        client.get("/api/health", headers={"Origin": "http://elsewhere.example"}).status_code == 200
    )


def test_no_cors_at_all(client: TestClient) -> None:
    res = client.options(
        "/api/runs",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code in (403, 405)
    for r in (res, client.get("/api/health", headers={"Origin": "http://evil.example"})):
        assert not any(h.lower().startswith("access-control-") for h in r.headers)


def test_strict_csp_and_no_store_on_the_api(client: TestClient) -> None:
    res = client.get("/api/health")
    csp = res.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-store"
    assert (
        res.headers["x-frame-options"] == "DENY" and res.headers["referrer-policy"] == "no-referrer"
    )
    assert "set-cookie" not in res.headers


def test_the_front_end_is_served_from_the_same_origin(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    assert "content-security-policy" in res.headers
    assert client.get("/app.js").status_code == 200


def test_oversized_bodies_are_refused(client: TestClient) -> None:
    big = b'{"compass_id":"' + b"x" * (300 * 1024) + b'"}'
    res = client.post("/api/runs", content=big, headers={"content-type": "application/json"})
    assert res.status_code == 413 and res.json()["error"]["code"] == "too_large"

    # also when the length is not declared up front (chunked)
    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(40):
            yield b"x" * 10_000

    res = client.post("/api/runs", content=chunks(), headers={"content-type": "application/json"})
    assert res.status_code == 413
