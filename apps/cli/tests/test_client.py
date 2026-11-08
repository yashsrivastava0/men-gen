"""The HTTP client: errors, retries, the token kept private, and the event stream (buffered and live)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import Any

import httpx
import pytest
import pytest_asyncio

from mde_cli.client import (
    ApiClient,
    ApiError,
    ApiUnreachable,
    Identity,
    SseParser,
    events_from_sse_text,
    make_http,
    parse_event,
)
from mde_context_api.composition import AppState
from mde_context_api.main import create_app
from mde_context_api.testing import TEST_TOKEN, serve_app
from mde_contracts.events import AssistantMessage

ME = Identity("tenant-a", "user-1")
OK_EVENT_RESPONSE = {"session_id": "ses_1", "events": [], "state": "DISCOVERING", "last_seq": 0}


def client_over(
    handler: Callable[[httpx.Request], Any],
    **kwargs: object,
) -> ApiClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.test")
    return ApiClient(http, "s3cret-token-value", ME, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- SSE parsing


def frame(seq: int, kind: str = "assistant_message", **fields: object) -> str:
    data = {
        "type": kind,
        "event_id": f"evt_{seq}",
        "session_id": "ses_1",
        "seq": seq,
        "text": "hello",
        **fields,
    }
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data)}\n\n"


def test_the_parser_joins_data_lines_skips_comments_and_closes_on_a_blank_line() -> None:
    parser = SseParser()
    lines = ["retry: 3000", "", ": keepalive", "id: 7", "event: card", "data: one", "data: two", ""]
    frames = [f for line in lines if (f := parser.feed(line)) is not None]
    assert len(frames) == 1
    assert (frames[0].id, frames[0].event, frames[0].data) == ("7", "card", "one\ntwo")
    assert parser.flush() is None
    parser.feed("data: trailing")
    last = parser.flush()
    assert last is not None and last.data == "trailing" and last.event == "message"


def test_a_body_becomes_typed_events_and_unknown_types_are_skipped() -> None:
    body = (
        "retry: 3000\n\n"
        + frame(1)
        + frame(2, "from_the_future", text="x")
        + ": keepalive\n\n"
        + frame(3)
    )
    events = events_from_sse_text(body)
    assert [e.seq for e in events] == [1, 3]
    assert all(isinstance(e, AssistantMessage) for e in events)
    assert parse_event(json.loads(frame(5).split("data: ")[1])).seq == 5


def test_a_stream_error_frame_raises_the_api_error() -> None:
    body = 'event: stream_error\ndata: {"code":"not_found","message":"Gone."}\n\n'
    with pytest.raises(ApiError) as raised:
        events_from_sse_text(body)
    assert (raised.value.code, raised.value.message) == ("not_found", "Gone.")
    with pytest.raises(ApiError) as bad:
        events_from_sse_text("event: stream_error\ndata: not json\n\n")
    assert bad.value.code == "stream_error"


def test_a_frame_cut_off_at_the_end_is_still_read() -> None:
    body = frame(1).rstrip("\n")  # no closing blank line
    assert [e.seq for e in events_from_sse_text(body)] == [1]


# --------------------------------------------------------------------------- errors


async def test_an_error_body_becomes_an_api_error_with_its_code_and_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "code": "hash_mismatch",
                "message": "Out of date.",
                "request_id": "req_9",
                "details": [{"field": "hash", "problem": "stale"}],
            },
        )

    client = client_over(handler)
    with pytest.raises(ApiError) as raised:
        await client.snapshot("ses_1")
    error = raised.value
    assert (error.status, error.code, error.message, error.request_id) == (
        409,
        "hash_mismatch",
        "Out of date.",
        "req_9",
    )
    assert error.details == [{"field": "hash", "problem": "stale"}]


async def test_a_body_that_is_not_json_still_gives_a_status_and_code() -> None:
    client = client_over(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ApiError) as raised:
        await client.snapshot("ses_1")
    assert (raised.value.status, raised.value.code) == (502, "http_502")


async def test_a_refused_connection_is_api_unreachable_with_a_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = client_over(handler, label="the API at http://x")
    with pytest.raises(ApiUnreachable) as raised:
        await client.healthz()
    assert "Cannot connect to the API at http://x" in str(raised.value)
    assert raised.value.hint is not None and "--local" in raised.value.hint


async def test_a_timeout_is_api_unreachable_without_the_connect_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiUnreachable) as raised:
        await client_over(handler).snapshot("ses_1")
    assert "did not answer in time" in str(raised.value) and raised.value.hint is None


async def test_a_slow_server_is_cut_off_by_the_overall_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    client = client_over(handler, timeout_s=0.1)
    with pytest.raises(ApiUnreachable):
        await client.healthz()


async def test_an_event_is_retried_with_the_same_id_after_a_dropped_connection() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            raise httpx.ConnectTimeout("dropped", request=request)
        return httpx.Response(200, json=OK_EVENT_RESPONSE)

    client = client_over(handler)
    reply = await client.post_event(
        "ses_1", {"type": "user_message", "event_id": "evt_1", "text": "x"}
    )
    assert reply.state == "DISCOVERING"
    assert len(seen) == 2 and seen[0] == seen[1] and seen[0]["event_id"] == "evt_1"


async def test_it_gives_up_after_the_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ApiUnreachable):
        await client_over(handler).post_event("ses_1", {"type": "ui_action"})
    assert calls == 3  # the first try and two retries


async def test_the_token_is_sent_but_never_shown() -> None:
    sent: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(request.headers)
        return httpx.Response(401, json={"code": "unauthorized", "message": "No."})

    client = client_over(handler)
    with pytest.raises(ApiError) as raised:
        await client.healthz()
    assert sent["authorization"] == "Bearer s3cret-token-value"
    assert sent["x-tenant-id"] == "tenant-a" and sent["x-user-id"] == "user-1"
    assert "x-roles" not in sent
    assert "s3cret-token-value" not in repr(client) + str(raised.value) + repr(
        raised.value.__dict__
    )
    approver = client.headers(Identity("tenant-a", "u-x", ("delivery_lead", "qa")))
    assert approver["X-Roles"] == "delivery_lead,qa" and approver["X-User-Id"] == "u-x"
    assert ME.acting_as("u-y", ("r",)) == Identity("tenant-a", "u-y", ("r",))


async def test_make_http_ignores_proxy_settings() -> None:
    http = make_http("http://127.0.0.1:9/")
    try:
        assert str(http.base_url) == "http://127.0.0.1:9"
    finally:
        await http.aclose()


# --------------------------------------------------------------------------- against the real app


async def test_sessions_events_and_documents_over_the_buffered_transport(api: ApiClient) -> None:
    created = await api.create_session("guided")
    sid = created.session.session_id
    assert created.events and isinstance(created.events[0], AssistantMessage)
    assert (await api.healthz())["status"] == "ok"
    status, body = await api.readyz()
    assert status == 200 and body["status"] == "ready"
    reply = await api.post_event(
        sid, {"type": "user_message", "event_id": "evt_a1", "text": "an online shop"}
    )
    assert reply.last_seq >= 2
    stored = await api.stored_events(sid)
    assert [e.seq for e in stored] == list(range(1, len(stored) + 1))
    later = await api.stored_events(sid, after=2)
    assert [e.seq for e in later] == [e.seq for e in stored if e.seq > 2]
    assert (await api.snapshot(sid)).state == "DISCOVERING"
    assert (await api.budget()).live_calls == 0
    with pytest.raises(ApiError) as no_brief:
        await api.brief(sid)
    assert no_brief.value.status == 404
    with pytest.raises(ApiError) as nope:
        await api.snapshot("ses_unknown")
    assert nope.value.code == "not_found"
    bad = ApiClient(api.http, "wrong", ME, buffered=True)
    with pytest.raises(ApiError) as raised:
        await bad.snapshot(sid)
    assert raised.value.status == 401


async def test_watch_over_the_buffered_transport_polls_until_its_time_is_up(api: ApiClient) -> None:
    sid = (await api.create_session("guided")).session.session_id
    started = asyncio.get_running_loop().time()
    events = await api.next_events(sid, after=0, timeout_s=1.0)
    assert [e.seq for e in events] == [1]
    assert asyncio.get_running_loop().time() - started >= 0.9  # it kept watching, then ended


# --------------------------------------------------------------------------- over a real socket


@pytest_asyncio.fixture
async def live(state: AppState) -> AsyncIterator[ApiClient]:
    """A client on a real localhost socket (an ASGI test transport buffers a whole response, so
    streaming needs a real server)."""
    async with serve_app(create_app(state=state)) as base_url:
        client = ApiClient(make_http(base_url), TEST_TOKEN, ME)
        try:
            yield client
        finally:
            await client.aclose()


async def test_the_stream_replays_after_last_event_id_over_a_real_socket(live: ApiClient) -> None:
    sid = (await live.create_session("guided")).session.session_id
    await live.post_event(
        sid, {"type": "user_message", "event_id": "evt_b1", "text": "an online shop"}
    )
    everything = await live.next_events(sid, after=0, timeout_s=1.0)
    assert len(everything) >= 2 and [e.seq for e in everything] == list(
        range(1, len(everything) + 1)
    )
    replay = await live.next_events(sid, after=1, timeout_s=1.0)  # Last-Event-ID: 1
    assert [e.seq for e in replay] == [e.seq for e in everything if e.seq > 1]


async def test_the_stream_delivers_a_new_event_while_it_is_open(live: ApiClient) -> None:
    created = await live.create_session("guided")
    sid, cursor = created.session.session_id, created.session.last_seq
    got: list[int] = []

    async def follow() -> None:
        async with aclosing(live.watch(sid, after=cursor, timeout_s=30)) as stream:
            async for event in stream:
                got.append(event.seq)
                if isinstance(event, AssistantMessage) and event.in_reply_to == "evt_c1":
                    return

    task = asyncio.create_task(follow())
    await asyncio.sleep(0.3)  # the stream is open and idle
    assert not got
    await live.post_event(
        sid, {"type": "user_message", "event_id": "evt_c1", "text": "an online shop"}
    )
    await asyncio.wait_for(task, timeout=15)
    assert got and all(seq > cursor for seq in got)


async def test_the_stream_of_a_foreign_session_is_a_404_before_streaming_starts(
    live: ApiClient,
) -> None:
    with pytest.raises(ApiError) as raised:
        await live.next_events("ses_nope", after=0, timeout_s=1.0)
    assert raised.value.status == 404


async def test_an_unreachable_stream_is_api_unreachable() -> None:
    client = ApiClient(make_http("http://127.0.0.1:1"), TEST_TOKEN, ME)  # nothing listens on port 1
    try:
        with pytest.raises(ApiUnreachable):
            await client.next_events("ses_1", after=0, timeout_s=1.0)
    finally:
        await client.aclose()
