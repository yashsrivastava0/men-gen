"""Resumable SSE: dense sequence, cursor by ``after`` and ``Last-Event-ID``, keepalives, stop at a terminal event,
reads only the store."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from mde_spec_agent.api.sse import event_stream
from mde_spec_agent.run.store import RunStore

Snap = dict[str, Any]
YOGA = "quick-yoga-studio-bengaluru"
NASHIK = "standard-produce-marketplace-nashik"
FREIGHT = "thorough-freight-fleet-india"


def frames(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "data" in fields:
            out.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return out


def finished(client: TestClient, start: Callable[..., Snap]) -> Snap:
    snap = start(YOGA)
    res = client.post(
        f"/api/runs/{snap['id']}/g2",
        json={"decision": "approve", "content_hash": snap["content_hash"]},
    )
    assert res.status_code == 200
    return snap


def test_stream_replays_everything_and_stops_at_the_terminal_event(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = finished(client, start)
    res = client.get(f"/api/runs/{snap['id']}/events")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    assert res.text.startswith("retry: 3000\n\n")
    got = frames(res.text)
    assert [f["id"] for f in got] == list(range(1, len(got) + 1))
    assert all(f["event"] == f["data"]["type"] and f["id"] == f["data"]["seq"] for f in got)
    assert got[-1]["event"] == "run.finished"


def test_after_cursor_returns_only_later_events(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = finished(client, start)
    everything = frames(client.get(f"/api/runs/{snap['id']}/events").text)
    later = frames(client.get(f"/api/runs/{snap['id']}/events?after=3").text)
    assert [f["id"] for f in later] == [f["id"] for f in everything if f["id"] > 3]
    assert frames(client.get(f"/api/runs/{snap['id']}/events?after={len(everything)}").text) == []


def test_last_event_id_resumes_and_after_wins(
    client: TestClient, start: Callable[..., Snap]
) -> None:
    snap = finished(client, start)
    n = len(frames(client.get(f"/api/runs/{snap['id']}/events").text))
    by_header = frames(
        client.get(f"/api/runs/{snap['id']}/events", headers={"Last-Event-ID": str(n - 2)}).text
    )
    assert [f["id"] for f in by_header] == [n - 1, n]
    both = frames(
        client.get(
            f"/api/runs/{snap['id']}/events?after={n - 1}", headers={"Last-Event-ID": "1"}
        ).text
    )
    assert [f["id"] for f in both] == [n]
    junk = client.get(f"/api/runs/{snap['id']}/events", headers={"Last-Event-ID": "abc"})
    assert len(frames(junk.text)) == n  # an unreadable id starts from the beginning


def test_unknown_run_and_bad_cursor(client: TestClient) -> None:
    assert client.get("/api/runs/run_NOPE00000000000000000000/events").status_code == 404
    assert client.get("/api/runs/run_NOPE00000000000000000000/events?after=-1").status_code in (
        400,
        404,
    )


async def _collect(gen: Any, limit: int) -> list[str]:
    out: list[str] = []
    async for chunk in gen:
        out.append(chunk)
        if len(out) >= limit:
            break
    return out


def test_active_run_tails_with_keepalives_and_new_events(tmp_path: Any) -> None:
    store = RunStore(tmp_path)
    rid = store.create_run("p", "h", {}).run_id
    store.append_event(rid, "run.started")

    async def go() -> list[str]:
        async def late() -> None:
            await asyncio.sleep(0.15)
            store.append_event(rid, "step.started", step="plan")

        asyncio.get_running_loop().create_task(late())
        gen = event_stream(
            store, rid, 0, is_active=lambda: True, keepalive_s=0.05, poll_s=0.02, max_seconds=1.0
        )
        return await _collect(gen, 5)

    chunks = asyncio.run(go())
    assert chunks[0] == "retry: 3000\n\n"
    assert chunks[1].startswith("id: 1\nevent: run.started")
    assert any(c == ": keepalive\n\n" for c in chunks)
    assert any(c.startswith("id: 2\nevent: step.started") for c in chunks)


def test_stream_of_a_finished_run_ends_without_the_store_being_active(tmp_path: Any) -> None:
    store = RunStore(tmp_path)
    rid = store.create_run("p", "h", {}).run_id
    store.append_event(rid, "run.started")
    store.append_event(rid, "run.partial", data={"reason": "stop"})
    store.append_event(rid, "run.resumed")  # after a terminal event: not sent in this pass

    async def go() -> list[str]:
        return await _collect(
            event_stream(store, rid, 0, is_active=lambda: False, poll_s=0.01, max_seconds=1.0), 10
        )

    chunks = asyncio.run(go())
    assert [c.split("\n")[1] for c in chunks[1:]] == ["event: run.started", "event: run.partial"]
