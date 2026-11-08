"""Every endpoint: happy path plus 401, 404, 409, 422 (httpx ASGITransport over ScriptedService)."""

from __future__ import annotations

import httpx

from mde_compass.api.schemas import ErrorBody, EventAck, SessionList, SessionSummary
from mde_compass.views.models import BriefViewModel

P = "/v1/compass"


def _msg(event_id: str, text: str = "a food delivery app in Pune") -> dict[str, object]:
    return {"type": "user_message", "event_id": event_id, "text": text}


async def _walk_to_review(client: httpx.AsyncClient, sid: str) -> None:
    await client.post(f"{P}/sessions/{sid}/events", json=_msg("e0"))
    for i in range(1, 9):
        r = await client.post(
            f"{P}/sessions/{sid}/events",
            json={"type": "card_you_decide", "event_id": f"e{i}", "card_id": f"Q-{i:03d}"},
        )
        assert r.status_code == 200, r.text


async def test_health_ready_and_root_redirect(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"


async def test_create_list_get_session(client: httpx.AsyncClient) -> None:
    r = await client.post(
        f"{P}/sessions", json={"mode": "enterprise", "locale": "hi", "user_label": "x"}
    )
    assert r.status_code == 201
    created = SessionSummary.model_validate(r.json())
    assert created.mode == "enterprise" and created.locale == "hi" and created.id.startswith("ses_")
    listing = SessionList.model_validate((await client.get(f"{P}/sessions")).json())
    assert [s.id for s in listing.sessions] == [created.id]
    got = SessionSummary.model_validate((await client.get(f"{P}/sessions/{created.id}")).json())
    assert got.id == created.id


async def test_create_session_validation_422(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{P}/sessions", json={"locale": "NOT A LOCALE"})
    assert r.status_code == 422
    body = ErrorBody.model_validate(r.json())
    assert body.code == "validation_failed"
    assert "NOT A LOCALE" not in body.message  # never echo the input


async def test_unknown_session_404_and_tenant_isolation(
    client: httpx.AsyncClient, session_id: str
) -> None:
    other = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t2", "X-User-Id": "u9"}
    for path in (
        f"/sessions/{session_id}",
        f"/sessions/{session_id}/brief",
        f"/sessions/{session_id}/brief/view",
        f"/sessions/{session_id}/brief/versions",
        f"/sessions/{session_id}/history",
        f"/sessions/{session_id}/calls",
        f"/sessions/{session_id}/handoff",
        f"/sessions/{session_id}/provenance/idea.summary",
    ):
        r = await client.get(P + path, headers=other)
        assert r.status_code == 404, path
        assert ErrorBody.model_validate(r.json()).code == "not_found"
    assert (await client.get(f"{P}/sessions/ses_missing")).status_code == 404
    assert (await client.get(f"{P}/sessions/ses_missing/stream")).status_code == 404
    r = await client.post(f"{P}/sessions/ses_missing/events", json=_msg("e1"))
    assert r.status_code == 404


async def test_events_ack_idempotent_and_card(client: httpx.AsyncClient, session_id: str) -> None:
    r = await client.post(f"{P}/sessions/{session_id}/events", json=_msg("e1"))
    assert r.status_code == 200
    ack = EventAck.model_validate(r.json())
    assert ack.accepted and not ack.duplicate
    types = [e.type for e in ack.events]
    assert "assistant_message" in types and "card" in types
    again = EventAck.model_validate(
        (await client.post(f"{P}/sessions/{session_id}/events", json=_msg("e1"))).json()
    )
    assert again.duplicate and again.events == []


async def test_events_validation_422(client: httpx.AsyncClient, session_id: str) -> None:
    url = f"{P}/sessions/{session_id}/events"
    for bad in (
        {"type": "nope", "event_id": "e1"},
        {"type": "user_message", "event_id": "bad id!", "text": "x"},
        {"type": "user_message", "event_id": "e2"},
        {"type": "user_message", "event_id": "e3", "text": "x", "extra": 1},
        {"type": "contributor_switch", "event_id": "e4"},
    ):
        r = await client.post(url, json=bad)
        assert r.status_code == 422, bad
        assert ErrorBody.model_validate(r.json()).code == "validation_failed"
    r = await client.post(url, content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


async def test_all_inbound_event_types_accepted(client: httpx.AsyncClient, session_id: str) -> None:
    await _walk_to_review(client, session_id)
    bodies = [
        {"type": "card_answer", "event_id": "a1", "card_id": "Q-001", "value": "A"},
        {"type": "card_skip", "event_id": "a2", "card_id": "Q-002"},
        {"type": "card_back", "event_id": "a3"},
        {"type": "upload_added", "event_id": "a4", "source_id": "SRC-001"},
        {"type": "link_added", "event_id": "a5", "url": "https://example.com"},
        {"type": "command", "event_id": "a6", "name": "undo"},
        {
            "type": "brief_feedback",
            "event_id": "a7",
            "target": "features",
            "reason": "too_much",
            "text": "x",
        },
        {"type": "assumption_decision", "event_id": "a8", "id": "ASM-001", "decision": "accept"},
        {"type": "lookup_edit", "event_id": "a9", "action": "add", "text": "refund rules"},
        {"type": "contributor_switch", "event_id": "a10", "contributor_id": "c1"},
    ]
    for body in bodies:
        r = await client.post(f"{P}/sessions/{session_id}/events", json=body)
        assert r.status_code == 200, (body, r.text)


async def test_brief_endpoints(client: httpx.AsyncClient, session_id: str) -> None:
    await _walk_to_review(client, session_id)
    brief = (await client.get(f"{P}/sessions/{session_id}/brief")).json()
    assert brief["version"] >= 1
    view = BriefViewModel.model_validate(
        (await client.get(f"{P}/sessions/{session_id}/brief/view")).json()
    )
    assert view.header.version >= 1 and len(view.sections) == 14
    hi = BriefViewModel.model_validate(
        (await client.get(f"{P}/sessions/{session_id}/brief/view", params={"lang": "hi"})).json()
    )
    assert hi.language == "hi" and hi.sections[0].title != view.sections[0].title
    assert (
        await client.get(f"{P}/sessions/{session_id}/brief/view", params={"lang": "xx"})
    ).status_code == 422
    versions = (await client.get(f"{P}/sessions/{session_id}/brief/versions")).json()["versions"]
    assert versions and versions[0]["version"] == 1
    await client.post(
        f"{P}/sessions/{session_id}/events",
        json={
            "type": "brief_edit",
            "event_id": "ed1",
            "item_id": "idea.summary",
            "value": "A new line",
        },
    )
    diff = (
        await client.get(f"{P}/sessions/{session_id}/brief/diff", params={"from": 1, "to": 2})
    ).json()
    assert diff["sections_changed"] == ["idea"] and diff["entries"][0]["kind"] == "changed"
    assert (
        await client.get(f"{P}/sessions/{session_id}/brief", params={"version": 99})
    ).status_code == 404
    r = await client.get(f"{P}/sessions/{session_id}/brief/diff", params={"from": 1})
    assert r.status_code == 422
    r = await client.post(
        f"{P}/sessions/{session_id}/events",
        json={"type": "brief_edit", "event_id": "ed2", "item_id": "no.such", "value": "x"},
    )
    assert r.status_code == 404


async def test_history_and_provenance(client: httpx.AsyncClient, session_id: str) -> None:
    await _walk_to_review(client, session_id)
    h = (await client.get(f"{P}/sessions/{session_id}/history", params={"limit": 3})).json()
    assert len(h["items"]) == 3 and h["next_after_idx"] is not None
    q = (await client.get(f"{P}/sessions/{session_id}/history", params={"q": "pune"})).json()
    assert q["items"]
    s = (
        await client.get(f"{P}/sessions/{session_id}/history", params={"slot": "idea.summary"})
    ).json()
    assert s["highlighted_turn_ids"]
    assert (
        await client.get(f"{P}/sessions/{session_id}/history", params={"limit": 0})
    ).status_code == 422
    p = (await client.get(f"{P}/sessions/{session_id}/provenance/idea.summary")).json()
    assert p["item_id"] == "idea.summary" and p["provenance"]
    nested = await client.get(f"{P}/sessions/{session_id}/provenance/scope.FTR-001.priority")
    assert nested.status_code == 404  # path-style ids route correctly, unknown id is a 404
    assert (await client.get(f"{P}/sessions/{session_id}/provenance/nope")).status_code == 404


async def test_gate_flow_409_then_approved_then_handoff(
    client: httpx.AsyncClient, session_id: str
) -> None:
    sid = session_id
    assert (await client.get(f"{P}/sessions/{sid}/handoff")).status_code == 409
    await _walk_to_review(client, sid)
    view = (await client.get(f"{P}/sessions/{sid}/brief/view")).json()
    assert view["footer"]["approvable"] is False and view["footer"]["blockers"]
    approve = {
        "type": "gate_decision",
        "event_id": "g1",
        "decision": "approve",
        "version": view["header"]["version"],
        "brief_hash": view["brief_hash"],
    }
    r = await client.post(f"{P}/sessions/{sid}/gate", json=approve)
    assert r.status_code == 409  # a high-impact assumption is still unreviewed
    await client.post(
        f"{P}/sessions/{sid}/events",
        json={
            "type": "assumption_decision",
            "event_id": "d1",
            "id": "ASM-001",
            "decision": "accept",
        },
    )
    view = (await client.get(f"{P}/sessions/{sid}/brief/view")).json()
    assert view["footer"]["approvable"] is True
    stale = {**approve, "event_id": "g2", "version": 1, "brief_hash": "deadbeef"}
    r = await client.post(f"{P}/sessions/{sid}/gate", json=stale)
    assert r.status_code == 409 and ErrorBody.model_validate(r.json()).code == "hash_mismatch"
    good = {
        **approve,
        "event_id": "g3",
        "version": view["header"]["version"],
        "brief_hash": view["brief_hash"],
    }
    r = await client.post(f"{P}/sessions/{sid}/gate", json=good)
    assert r.status_code == 200 and r.json()["baseline"] == "B-G1"
    h = (await client.get(f"{P}/sessions/{sid}/handoff")).json()
    assert h["brief_hash"] == view["brief_hash"] and set(h["views"]) >= {"research", "blueprint"}
    r = await client.post(
        f"{P}/sessions/{sid}/gate", json={"event_id": "g4", "decision": "maybe", "version": 1}
    )
    assert r.status_code == 422


async def test_calls_budget_strings_uploads(client: httpx.AsyncClient, session_id: str) -> None:
    await client.post(f"{P}/sessions/{session_id}/events", json=_msg("e1"))
    calls = (await client.get(f"{P}/sessions/{session_id}/calls")).json()["calls"]
    assert calls and {"spec_id", "tokens_in", "latency_ms"} <= set(calls[0])
    b = (await client.get(f"{P}/budget")).json()
    assert b["cap_usd"] == "2.00" and b["mode"] == "mock"
    en = (await client.get(f"{P}/strings/en")).json()
    assert en["strings"]["ui.looks_right"] == "Looks right"
    hi = (await client.get(f"{P}/strings/hi")).json()
    assert set(hi["strings"]) == set(en["strings"])
    assert (await client.get(f"{P}/strings/fr")).status_code == 404
    r = await client.post(f"{P}/sessions/{session_id}/uploads", files={"file": ("a.txt", b"hello")})
    assert r.status_code == 501 and r.json()["code"] == "not_implemented"


async def test_unknown_route_is_error_body(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{P}/nothing")
    assert r.status_code == 404 and ErrorBody.model_validate(r.json()).code == "not_found"
