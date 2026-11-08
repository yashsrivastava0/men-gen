"""SSE: frames, Last-Event-ID replay, follow=false, cursor parsing, stream cap, back-off."""

from __future__ import annotations

import json

import httpx
import pytest

from mde_compass.api.settings import ApiSettings
from mde_compass.api.sse import event_stream, parse_last_event_id
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app
from mde_compass.engine.ports import Caller

P = "/v1/compass"
GOOD = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}


def parse_frames(text: str) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                fields["comment"] = line[1:].strip()
            elif ":" in line:
                k, _, v = line.partition(":")
                fields[k] = v.strip()
        if fields:
            frames.append(fields)
    return frames


async def _seed(client: httpx.AsyncClient, sid: str) -> None:
    for i in range(2):
        await client.post(
            f"{P}/sessions/{sid}/events",
            json={"type": "user_message", "event_id": f"e{i}", "text": "hello there"},
        )


async def test_stream_frames_and_headers(client: httpx.AsyncClient, session_id: str) -> None:
    await _seed(client, session_id)
    r = await client.get(f"{P}/sessions/{session_id}/stream", params={"follow": "false"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache, no-transform"
    assert r.headers["x-accel-buffering"] == "no"
    assert r.text.startswith("retry: 3000\n\n")
    frames = [f for f in parse_frames(r.text) if "id" in f]
    ids = [int(f["id"]) for f in frames]
    assert ids == sorted(ids) and ids[0] == 1 and len(ids) >= 5
    first = json.loads(frames[0]["data"])
    assert (
        first["seq"] == 1
        and first["direction"] == "out"
        and first["payload"]["type"] == frames[0]["event"]
    )


async def test_replay_with_last_event_id(client: httpx.AsyncClient, session_id: str) -> None:
    await _seed(client, session_id)
    url = f"{P}/sessions/{session_id}/stream"
    full = [
        f
        for f in parse_frames((await client.get(url, params={"follow": "false"})).text)
        if "id" in f
    ]
    assert len(full) > 3
    first3 = [int(f["id"]) for f in full[:3]]
    assert first3 == [1, 2, 3]
    r = await client.get(url, params={"follow": "false"}, headers={"Last-Event-ID": "2"})
    later = [int(f["id"]) for f in parse_frames(r.text) if "id" in f]
    assert later == [int(f["id"]) for f in full if int(f["id"]) > 2]
    assert later[0] == 3
    # ?after= is the fallback when there is no header; the header wins when both are present
    r2 = await client.get(url, params={"follow": "false", "after": "4"})
    assert next(int(f["id"]) for f in parse_frames(r2.text) if "id" in f) == 5
    r3 = await client.get(
        url, params={"follow": "false", "after": "4"}, headers={"Last-Event-ID": "1"}
    )
    assert next(int(f["id"]) for f in parse_frames(r3.text) if "id" in f) == 2
    # junk header is ignored (replay from ?after / start), never an error
    r4 = await client.get(url, params={"follow": "false"}, headers={"Last-Event-ID": "9" * 40})
    assert (
        r4.status_code == 200
        and next(int(f["id"]) for f in parse_frames(r4.text) if "id" in f) == 1
    )
    # past the end: only the retry frame
    r5 = await client.get(url, params={"follow": "false"}, headers={"Last-Event-ID": "999"})
    assert [f for f in parse_frames(r5.text) if "id" in f] == []


def test_parse_last_event_id() -> None:
    assert parse_last_event_id("17") == 17
    assert parse_last_event_id(" 5 ") == 5
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("") is None
    assert parse_last_event_id("-1") is None
    assert parse_last_event_id("1e3") is None
    assert parse_last_event_id("١٢") is None  # non-ASCII digits
    assert parse_last_event_id("1" * 13) is None
    assert parse_last_event_id("9" * 12) == 2_147_483_647  # clamped


async def test_stream_validates_after_param(client: httpx.AsyncClient, session_id: str) -> None:
    r = await client.get(f"{P}/sessions/{session_id}/stream", params={"after": "-3"})
    assert r.status_code == 422


async def test_stream_tail_keepalive_and_backoff() -> None:
    """Drive ``event_stream`` with a fake clock: keepalive after 15 s, back-off between empty polls."""
    service = ScriptedService()
    caller = Caller("t1", "u1")
    rec = await service.create_session(caller, mode="guided", locale="en", user_label="")
    now = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)
        now["t"] += d

    gen = event_stream(
        service,
        caller,
        rec.id,
        poll_seconds=1.0,
        max_poll_seconds=4.0,
        keepalive_seconds=15.0,
        max_seconds=40.0,
        sleep=fake_sleep,
        monotonic=lambda: now["t"],
    )
    frames = [f async for f in gen]
    assert frames[0] == b"retry: 3000\n\n"
    assert frames[1].startswith(b"id: 1\n")
    assert b": keepalive\n\n" in frames
    assert (
        sleeps[:3] == [1.0, 1.0, 1.5] and max(sleeps) <= 4.0
    )  # backs off after empty polls, capped
    assert now["t"] <= 41.0  # ended at max_seconds, no overshoot


async def test_stream_picks_up_new_events_while_open() -> None:
    service = ScriptedService()
    caller = Caller("t1", "u1")
    rec = await service.create_session(caller, mode="guided", locale="en", user_label="")
    from mde_compass.domain.events import UserMessage

    sent = {"n": 0}

    async def fake_sleep(_: float) -> None:
        if sent["n"] == 0:
            sent["n"] = 1
            await service.handle_event(caller, rec.id, UserMessage(event_id="e1", text="hi"))

    out: list[bytes] = []
    gen = event_stream(
        service,
        caller,
        rec.id,
        poll_seconds=0.1,
        max_seconds=0.5,
        sleep=fake_sleep,
        monotonic=iter([0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 0.9] * 5).__next__,
    )
    async for frame in gen:
        out.append(frame)
    ids = [f for f in out if f.startswith(b"id: ")]
    assert len(ids) >= 3  # the greeting, then the events produced while the stream was open


async def test_per_caller_stream_cap() -> None:
    settings = ApiSettings(max_streams_per_caller=1, sse_poll_seconds=0.01)
    app = create_app(ScriptedService(), settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=GOOD
    ) as c:
        sid = (await c.post(f"{P}/sessions", json={})).json()["id"]
        app.state.stream_counts[("t1", "u1")] = 1  # one stream already open
        r = await c.get(f"{P}/sessions/{sid}/stream", params={"follow": "false"})
        assert r.status_code == 429 and r.json()["code"] == "too_many_streams"
        app.state.stream_counts[("t1", "u1")] = 0
        r = await c.get(f"{P}/sessions/{sid}/stream", params={"follow": "false"})
        assert r.status_code == 200
        assert app.state.stream_counts[("t1", "u1")] == 0  # released when the stream ended


async def test_stream_error_frame_when_session_vanishes() -> None:
    class Vanishing(ScriptedService):
        async def list_outbound(self, caller, session_id, *, after_seq=0, limit=200):  # type: ignore[no-untyped-def]
            if after_seq >= 1:
                from mde_core.errors import NotFound

                raise NotFound("gone")
            return await super().list_outbound(caller, session_id, after_seq=after_seq, limit=limit)

    service = Vanishing()
    caller = Caller("t1", "u1")
    rec = await service.create_session(caller, mode="guided", locale="en", user_label="")

    async def no_sleep(_: float) -> None:
        return None

    frames = [f async for f in event_stream(service, caller, rec.id, sleep=no_sleep)]
    assert any(f.startswith(b"event: stream_error") for f in frames)


@pytest.mark.parametrize("bad", ["abc", "1 2"])
def test_parse_last_event_id_rejects(bad: str) -> None:
    assert parse_last_event_id(bad) is None
