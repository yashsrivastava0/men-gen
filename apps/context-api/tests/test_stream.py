"""Server-sent events: replay from ``Last-Event-ID``, the live tail, keepalives, bounded reads.

Nothing here can hang: every stream is bounded (``timeout``/``limit``/``follow=false``) and every
wait has an explicit timeout. ASGI test transports buffer a whole response, so the incremental
behaviour is also checked against a real localhost server (``serve_app``).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mde_context_api import sse
from mde_context_api.composition import AppState
from mde_context_api.main import create_app
from mde_context_api.testing import (
    Api,
    headers,
    open_units,
    parse_events,
    parse_sse,
    serve_app,
)
from mde_contracts.common import Principal
from mde_contracts.events import AssistantMessage, StateChanged
from mde_core.errors import NotFound

PRINCIPAL = Principal(tenant_id="tenant-a", user_id="user-1")


async def conversation(api: Api) -> list[int]:
    """A short conversation; returns the seqs of everything stored."""
    await api.create()
    await api.say("hi")
    await api.to_gate1()
    return [e.seq for e in await api.refresh()]


async def test_replay_returns_every_stored_event_in_order(api: Api) -> None:
    seqs = await conversation(api)
    resp = await api.get("/stream", params={"follow": "false"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache, no-transform"
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.text.startswith("retry: 3000\n\n")
    frames = parse_sse(resp.text)
    assert [int(f["id"]) for f in frames] == seqs == list(range(1, len(seqs) + 1))
    events = parse_events([json.loads(f["data"]) for f in frames])
    assert [e.type for e in events] == [f["event"] for f in frames]
    assert events[0].type == "assistant_message" and events[-1].type == "gate"


async def test_frames_have_no_raw_newlines_inside_data(api: Api) -> None:
    await conversation(api)
    resp = await api.get("/stream", params={"follow": "false"})
    for block in resp.text.split("\n\n")[1:-1]:
        assert [line.split(":", 1)[0] for line in block.split("\n")] == ["id", "event", "data"]


async def test_last_event_id_resumes_after_that_seq(api: Api) -> None:
    seqs = await conversation(api)
    cut = seqs[len(seqs) // 2]
    resp = await api.get(
        "/stream", params={"follow": "false"}, headers={**api.headers, "Last-Event-ID": str(cut)}
    )
    assert [int(f["id"]) for f in parse_sse(resp.text)] == [s for s in seqs if s > cut]


async def test_the_after_query_is_the_same_cursor_for_clients_without_headers(api: Api) -> None:
    seqs = await conversation(api)
    resp = await api.get("/stream", params={"follow": "false", "after": seqs[-3]})
    assert [int(f["id"]) for f in parse_sse(resp.text)] == seqs[-2:]


async def test_the_header_wins_over_the_query_and_garbage_means_from_the_start(api: Api) -> None:
    seqs = await conversation(api)
    both = await api.get(
        "/stream",
        params={"follow": "false", "after": 1},
        headers={**api.headers, "Last-Event-ID": str(seqs[-1] - 1)},
    )
    assert [int(f["id"]) for f in parse_sse(both.text)] == [seqs[-1]]
    junk = await api.get(
        "/stream", params={"follow": "false"}, headers={**api.headers, "Last-Event-ID": "not-a-seq"}
    )
    assert [int(f["id"]) for f in parse_sse(junk.text)] == seqs


async def test_resuming_past_the_end_sends_nothing_and_closes(api: Api) -> None:
    seqs = await conversation(api)
    resp = await api.get("/stream", params={"follow": "false", "after": seqs[-1]})
    assert parse_sse(resp.text) == [] and resp.text == "retry: 3000\n\n"


async def test_limit_closes_the_stream_after_that_many_events(api: Api) -> None:
    await conversation(api)
    resp = await api.get("/stream", params={"limit": 2})  # follow stays on; limit ends it
    assert [int(f["id"]) for f in parse_sse(resp.text)] == [1, 2]


async def test_replay_in_small_batches_loses_nothing(
    api: Api, state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    seqs = await conversation(api)
    monkeypatch.setattr(state.settings, "sse_batch_size", 2)
    resp = await api.get("/stream", params={"follow": "false"})
    assert [int(f["id"]) for f in parse_sse(resp.text)] == seqs


async def test_a_live_tail_delivers_events_that_arrive_while_it_is_open(api: Api) -> None:
    await api.create()
    greeting = api.events[0]
    assert isinstance(greeting, AssistantMessage)

    async def tail() -> list[dict[str, str]]:
        resp = await api.get("/stream", params={"after": 1, "limit": 2, "timeout": 8})
        return parse_sse(resp.text)

    async with asyncio.timeout(15):
        reader = asyncio.create_task(tail())
        await asyncio.sleep(0.15)  # the stream is open and idle
        assert not reader.done()
        await api.say(greeting.chips[0])  # a reply and the first card: events 2 and 3
        frames = await reader
    assert [int(f["id"]) for f in frames] == [2, 3]
    assert [f["event"] for f in frames] == ["assistant_message", "card"]


async def test_an_idle_stream_sends_keepalives_and_ends_at_its_timeout(api: Api) -> None:
    seqs = await conversation(api)
    resp = await api.get("/stream", params={"after": seqs[-1], "timeout": 0.7})
    assert resp.status_code == 200
    assert resp.text.count(": keepalive\n\n") >= 2  # every 0.2 s in these tests
    assert parse_sse(resp.text) == []


async def test_a_foreign_or_unknown_session_is_a_404_not_a_broken_stream(
    client: httpx.AsyncClient, api: Api
) -> None:
    await api.create()
    other = await client.get(
        f"/v1/sessions/{api.session_id}/stream", headers=headers(tenant="tenant-b")
    )
    nothing = await client.get("/v1/sessions/ses_nope/stream", headers=headers())
    for resp in (other, nothing):
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json()["code"] == "not_found"


async def test_a_stream_error_mid_flight_is_reported_and_closes(
    state: AppState, api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await api.create()
    calls = 0
    real = state.flow.list_events

    async def flaky(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls > 1:
            raise NotFound("session gone")
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(state.flow, "list_events", flaky)
    frames = [
        f
        async for f in sse.event_stream(
            state.flow, PRINCIPAL, api.session_id, after_seq=1, poll_seconds=0.01
        )
    ]
    assert frames[0] == sse.RETRY_FRAME
    assert frames[-1].startswith(b"event: stream_error\n")
    assert json.loads(frames[-1].split(b"data: ")[1])["code"] == "not_found"


async def test_a_stream_never_holds_a_connection_between_polls(state: AppState, api: Api) -> None:
    await conversation(api)
    cursor = max(e.seq for e in api.events)
    stream = sse.event_stream(
        state.flow,
        PRINCIPAL,
        api.session_id,
        after_seq=cursor,
        poll_seconds=0.3,
        keepalive_seconds=0.05,
        max_seconds=5,
    )
    assert await anext(stream) == sse.RETRY_FRAME
    assert open_units(state) == 0  # suspended at a yield: nothing held
    pending = asyncio.ensure_future(anext(stream))  # keepalive, then it sleeps 0.3 s
    async with asyncio.timeout(5):
        assert await pending == sse.KEEPALIVE
        next_frame = asyncio.ensure_future(anext(stream))
        samples: list[int] = []
        for _ in range(5):
            await asyncio.sleep(0.04)  # inside the poll sleep
            samples.append(open_units(state))
        assert samples == [0] * 5
        assert await next_frame == sse.KEEPALIVE
    await stream.aclose()
    assert open_units(state) == 0


async def test_many_open_streams_do_not_starve_the_pool(
    state: AppState, api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More idle streams than the Postgres pool has connections (5), and the API still answers.

    (One caller opening twelve streams is refused by the per-caller cap, which this test is not
    about: raise it so the pool is what is being tested.)"""
    monkeypatch.setattr(state.settings, "api_max_streams_per_principal", 50)
    await conversation(api)
    cursor = max(e.seq for e in api.events)
    transport = httpx.ASGITransport(app=create_app(state=state), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as many:

        async def idle_stream() -> httpx.Response:
            return await many.get(
                f"/v1/sessions/{api.session_id}/stream",
                params={"after": cursor, "timeout": 1.5},
                headers=api.headers,
            )

        async with asyncio.timeout(20):
            streams = [asyncio.create_task(idle_stream()) for _ in range(12)]
            await asyncio.sleep(0.5)
            assert (await api.get()).status_code == 200  # a normal request is not blocked
            tap = await api.say("hello again")
            assert tap
            done = await asyncio.gather(*streams)
    assert all(r.status_code == 200 for r in done)
    assert open_units(state) == 0


async def test_real_streaming_over_a_socket_replays_then_follows_live(
    mem_state: AppState,
) -> None:
    """The first frames arrive at once, a later event arrives while the response is still open,
    and a reconnect with Last-Event-ID resumes without a duplicate."""
    app = create_app(state=mem_state)
    async with serve_app(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        hdrs = headers()
        created = await client.post("/v1/sessions", json={}, headers=hdrs)
        sid = created.json()["session"]["session_id"]
        url = f"/v1/sessions/{sid}/stream"

        seen: list[tuple[int, str]] = []
        async with asyncio.timeout(15):
            async with client.stream("GET", url, headers=hdrs, params={"timeout": 10}) as resp:
                assert resp.status_code == 200
                lines = resp.aiter_lines()
                event_id = 0
                async for line in lines:  # the replayed greeting: before anything else happens
                    if line.startswith("id: "):
                        event_id = int(line[4:])
                    if line.startswith("event: "):
                        seen.append((event_id, line[7:]))
                        break
                assert seen == [(1, "assistant_message")]

                greeting = created.json()["events"][0]
                sent = await client.post(  # a new event while the stream is open
                    f"/v1/sessions/{sid}/events",
                    headers=hdrs,
                    json={
                        "type": "user_message",
                        "event_id": "evt-live-1",
                        "text": greeting["chips"][0],
                    },
                )
                assert sent.status_code == 200
                expected = {e["seq"] for e in sent.json()["events"]}
                live: set[int] = set()
                async for line in lines:
                    if line.startswith("id: "):
                        live.add(int(line[4:]))
                    if live >= expected:
                        break
                assert expected <= live  # arrived over the still-open response

        # reconnect after the greeting: only newer events, none twice
        async with asyncio.timeout(15):
            replay = await client.get(
                url, headers={**hdrs, "Last-Event-ID": "1"}, params={"follow": "false"}
            )
        ids = [int(f["id"]) for f in parse_sse(replay.text)]
        assert ids == sorted(expected) and 1 not in ids


async def test_a_client_that_disconnects_leaves_nothing_behind(mem_state: AppState) -> None:
    app = create_app(state=mem_state)
    async with serve_app(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        hdrs = headers()
        sid = (await client.post("/v1/sessions", json={}, headers=hdrs)).json()["session"][
            "session_id"
        ]
        async with asyncio.timeout(15):
            async with client.stream(
                "GET", f"/v1/sessions/{sid}/stream", headers=hdrs, params={"timeout": 30}
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        break  # leave while the server would keep following
            await asyncio.sleep(0.2)
            assert open_units(mem_state) == 0
            assert (await client.get("/healthz")).status_code == 200  # the server carries on


@pytest.mark.parametrize(
    ("raw", "cursor"),
    [
        (None, None),
        (" 17 ", 17),
        ("0", 0),
        ("000000000017", 17),  # 12 characters: fine
        ("999999999999", sse.MAX_CURSOR),  # 12 digits: clamped to the largest stored seq
        ("2147483647", 2_147_483_647),
        ("2147483648", sse.MAX_CURSOR),
        ("9" * 13, None),  # too long: junk, ignored
        ("9" * 5000, None),
        ("1" * 4301, None),  # past int()'s own digit limit: must not raise
        ("-3", None),
        ("+3", None),
        ("1.5", None),
        ("abc", None),
        ("", None),
        ("   ", None),
        ("²", None),  # superscript two: isdigit() but not a number int() accepts
        ("١٢", None),  # Arabic-Indic digits
        ("１２", None),  # full-width digits
        ("1_000", None),
    ],
    ids=lambda v: str(v)[:12],
)
def test_last_event_id_is_parsed_safely(raw: str | None, cursor: int | None) -> None:
    assert sse.parse_last_event_id(raw) == cursor


@pytest.mark.parametrize(
    "junk",
    ["9" * 5000, "9" * 13, "²".encode("latin-1"), "1" * 4301],
    ids=["5000-digits", "13-digits", "sup-two", "4301"],
)
async def test_a_hostile_last_event_id_starts_from_the_beginning_and_is_never_a_500(
    api: Api, junk: str | bytes
) -> None:
    seqs = await conversation(api)
    resp = await api.get(
        "/stream",
        params={"follow": "false"},
        headers={**api.headers, "Last-Event-ID": junk},  # type: ignore[dict-item]
    )
    assert resp.status_code == 200
    assert [int(f["id"]) for f in parse_sse(resp.text)] == seqs


async def test_a_huge_but_legal_last_event_id_means_nothing_new(api: Api) -> None:
    await conversation(api)
    resp = await api.get(
        "/stream",
        params={"follow": "false"},
        headers={**api.headers, "Last-Event-ID": "999999999999"},
    )
    assert resp.status_code == 200 and parse_sse(resp.text) == []


async def test_the_after_query_is_bounded_too(api: Api) -> None:
    await api.create()
    resp = await api.get("/stream", params={"follow": "false", "after": "9" * 30})
    assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"
    ok = await api.get("/stream", params={"follow": "false", "after": sse.MAX_CURSOR})
    assert ok.status_code == 200


def test_the_helpers_parse_cursors_and_format_frames() -> None:
    assert sse.parse_last_event_id(None) is None
    assert sse.parse_last_event_id(" 17 ") == 17
    assert sse.parse_last_event_id("-3") is None and sse.parse_last_event_id("abc") is None
    event = StateChanged(
        event_id="evt_1",
        session_id="ses_1",
        seq=9,
        from_state="DISCOVERING",
        to_state="BRIEF_REVIEW",
    )
    frame = sse.format_event(event).decode()
    assert frame.startswith("id: 9\nevent: state_changed\ndata: {") and frame.endswith("}\n\n")
    assert "\n" not in frame.split("data: ", 1)[1].rstrip("\n")
    assert (
        sse.format_error("x", "y") == b'event: stream_error\ndata: {"code":"x","message":"y"}\n\n'
    )
