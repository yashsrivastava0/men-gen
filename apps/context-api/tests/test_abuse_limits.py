"""N6: abuse limits. A cap on concurrent event streams per caller (with slots that always come
back), an idle stream that polls less and less often, and a sliding-window rate limit on session
creation per caller and per tenant. Time is a fake clock everywhere it matters; nothing sleeps for
real except the few tests that need a real socket."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from mde_context_api import sse
from mde_context_api.composition import AppState
from mde_context_api.errors import TooManyRequests, TooManyStreams
from mde_context_api.limits import (
    STREAM_LEASE_GRACE_SECONDS,
    AbuseLimits,
    SessionRateLimiter,
    StreamLimiter,
)
from mde_context_api.main import create_app
from mde_context_api.testing import (
    Api,
    build_state,
    eventually,
    headers,
    make_client,
    make_settings,
    serve_app,
)
from mde_contracts.common import Principal
from mde_contracts.events import AssistantMessage, OutboundEvent
from mde_core.clock import FakeClock
from mde_core.errors import NotFound

ALICE = Principal(tenant_id="acme", user_id="alice")
BOB = Principal(tenant_id="acme", user_id="bob")
ZED = Principal(tenant_id="zeta", user_id="alice")  # same user name, another tenant


# --------------------------------------------------------------------------- stream slots (unit)


def streams(limit: int, clock: FakeClock | None = None) -> StreamLimiter:
    return StreamLimiter(clock or FakeClock(), lambda: limit)


def test_the_sixth_stream_of_one_caller_is_refused_with_a_friendly_message() -> None:
    limiter = streams(5)
    leases = [limiter.acquire(ALICE, ttl_seconds=900) for _ in range(5)]
    assert limiter.active(ALICE) == 5
    with pytest.raises(TooManyStreams) as refused:
        limiter.acquire(ALICE, ttl_seconds=900)
    assert refused.value.status == 429 and refused.value.code == "too_many_streams"
    assert "5 live streams" in refused.value.message and "Retry-After" in refused.value.headers
    assert limiter.active(ALICE) == 5  # the refused one took nothing
    other_user = limiter.acquire(BOB, ttl_seconds=900)  # the cap is per caller ...
    other_tenant = limiter.acquire(ZED, ttl_seconds=900)  # ... and a tenant name is part of it
    assert other_user is not None and other_tenant is not None
    assert all(lease is not None for lease in leases)


def test_releasing_a_lease_frees_the_slot_and_a_second_release_does_nothing() -> None:
    limiter = streams(2)
    first = limiter.acquire(ALICE, ttl_seconds=900)
    second = limiter.acquire(ALICE, ttl_seconds=900)
    assert first is not None and second is not None
    with pytest.raises(TooManyStreams):
        limiter.acquire(ALICE, ttl_seconds=900)
    first.release()
    first.release()  # idempotent: it must not free someone else's slot
    assert limiter.active(ALICE) == 1
    third = limiter.acquire(ALICE, ttl_seconds=900)
    assert third is not None and limiter.active(ALICE) == 2
    with pytest.raises(TooManyStreams):
        limiter.acquire(ALICE, ttl_seconds=900)


def test_a_lease_that_was_never_released_expires_by_itself() -> None:
    """A stream that never reached its ``finally`` cannot hold a slot for ever."""
    clock = FakeClock()
    limiter = streams(1, clock)
    assert limiter.acquire(ALICE, ttl_seconds=60) is not None
    with pytest.raises(TooManyStreams):
        limiter.acquire(ALICE, ttl_seconds=60)
    clock.advance(60 + STREAM_LEASE_GRACE_SECONDS - 1)
    assert limiter.active(ALICE) == 1
    clock.advance(2)
    assert limiter.active(ALICE) == 0
    assert limiter.acquire(ALICE, ttl_seconds=60) is not None


def test_a_cap_of_zero_turns_the_limit_off_and_the_cap_is_read_per_request() -> None:
    cap = {"n": 0}
    limiter = StreamLimiter(FakeClock(), lambda: cap["n"])
    assert all(limiter.acquire(ALICE, ttl_seconds=1) is None for _ in range(20))
    cap["n"] = 1
    assert limiter.acquire(ALICE, ttl_seconds=1) is not None
    with pytest.raises(TooManyStreams):
        limiter.acquire(ALICE, ttl_seconds=1)


# --------------------------------------------------------------------------- stream slots (HTTP)


def hold(client: httpx.AsyncClient, session_id: str, seconds: float = 1.5) -> asyncio.Task[Any]:
    return asyncio.create_task(
        client.get(
            f"/v1/sessions/{session_id}/stream", params={"timeout": seconds}, headers=headers()
        )
    )


async def test_the_sixth_live_stream_gets_a_429_and_the_slot_returns_when_they_end(
    api: Api, state: AppState
) -> None:
    await api.create()
    principal = Principal(tenant_id="tenant-a", user_id="user-1")
    async with asyncio.timeout(20):
        held = [hold(api.client, api.session_id) for _ in range(5)]
        await eventually(_active(state, principal, 5), within=5)
        sixth = await api.client.get(api.url("/stream"), headers=api.headers)
        assert sixth.status_code == 429
        body = sixth.json()
        assert body["code"] == "too_many_streams" and "live streams" in body["message"]
        assert sixth.headers["retry-after"] == "5"
        assert sixth.headers["content-type"].startswith("application/json")
        # a bounded read does not need a slot at all
        replay = await api.get("/stream", params={"follow": "false"})
        assert replay.status_code == 200
        done = await asyncio.gather(*held)
        assert all(r.status_code == 200 for r in done)
        await eventually(_active(state, principal, 0), within=5)
        again = await api.client.get(
            api.url("/stream"), params={"timeout": 0.2}, headers=api.headers
        )
        assert again.status_code == 200  # a slot is free again


def _active(state: AppState, principal: Principal, expected: int):  # type: ignore[no-untyped-def]
    async def check() -> bool:
        return state.limits.streams.active(principal) == expected

    return check


async def test_a_stream_that_cannot_start_does_not_keep_a_slot(api: Api, state: AppState) -> None:
    """Unknown and foreign sessions are a 404 before any streaming: ten in a row never exhaust the
    cap, because each gives its slot back."""
    principal = Principal(tenant_id="tenant-a", user_id="user-1")
    for _ in range(12):
        resp = await api.client.get("/v1/sessions/ses_nope/stream", headers=headers())
        assert resp.status_code == 404
    assert state.limits.streams.active(principal) == 0


async def test_a_stream_that_ends_in_an_error_gives_its_slot_back(
    api: Api, state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    await api.create()
    principal = Principal(tenant_id="tenant-a", user_id="user-1")
    real = state.flow.list_events
    plan: dict[str, Any] = {"calls": 0, "fail_at": None, "error": None}

    async def flaky(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        plan["calls"] += 1
        if plan["calls"] == plan["fail_at"]:
            raise plan["error"]
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    def next_request(fail_at: int | None, error: Exception | None) -> None:
        plan.update(calls=0, fail_at=fail_at, error=error)

    monkeypatch.setattr(state.flow, "list_events", flaky)
    monkeypatch.setattr(state.settings, "api_max_streams_per_principal", 1)
    async with asyncio.timeout(15):
        # the session vanishes mid-stream: a stream_error frame, then the stream ends
        next_request(2, NotFound("session gone"))
        first = await api.client.get(api.url("/stream"), headers=api.headers)
        assert b"stream_error" in first.content
        assert state.limits.streams.active(principal) == 0
        # an unexpected failure mid-stream: the response breaks, the slot still comes back
        next_request(2, RuntimeError("boom"))
        second = await api.client.get(api.url("/stream"), headers=api.headers)
        assert second.status_code in (200, 500)
        assert state.limits.streams.active(principal) == 0
        # the very first read fails (a 500 before any streaming): nothing is held either
        next_request(1, RuntimeError("boom"))
        third = await api.client.get(api.url("/stream"), headers=api.headers)
        assert third.status_code == 500
        assert state.limits.streams.active(principal) == 0
        next_request(None, None)
        last = await api.client.get(
            api.url("/stream"), params={"timeout": 0.2}, headers=api.headers
        )
        assert last.status_code == 200  # the only slot is free: nothing leaked


async def test_a_client_that_disconnects_gives_its_slot_back(mem_state: AppState) -> None:
    """Over a real socket: the client drops five live streams, the next one is accepted."""
    mem_state.settings.api_max_streams_per_principal = 5
    principal = Principal(tenant_id="tenant-a", user_id="user-1")
    app = create_app(state=mem_state)
    async with serve_app(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        hdrs = headers()
        sid = (await client.post("/v1/sessions", json={}, headers=hdrs)).json()["session"][
            "session_id"
        ]
        url = f"/v1/sessions/{sid}/stream"
        async with asyncio.timeout(20):
            opened: list[Any] = []
            for _ in range(5):
                cm = client.stream("GET", url, headers=hdrs, params={"timeout": 30})
                resp = await cm.__aenter__()
                opened.append((cm, resp))
            await eventually(_active(mem_state, principal, 5), within=5)
            refused = await client.get(url, headers=hdrs, params={"timeout": 30})
            assert refused.status_code == 429
            for cm, _resp in opened:  # hang up
                await cm.__aexit__(None, None, None)
            await eventually(_active(mem_state, principal, 0), within=10)
            ok = await client.get(url, headers=hdrs, params={"timeout": 0.3})
            assert ok.status_code == 200


# --------------------------------------------------------------------------- idle back-off


class ScriptedFlow:
    """``list_events`` answers from a script: one list per poll, then empty lists."""

    def __init__(self, script: Sequence[Sequence[OutboundEvent]] = ()) -> None:
        self.script = [list(batch) for batch in script]
        self.polls = 0

    async def list_events(self, *args: object, **kwargs: object) -> list[OutboundEvent]:
        self.polls += 1
        return self.script.pop(0) if self.script else []


def message(seq: int) -> OutboundEvent:
    return AssistantMessage(event_id=f"evt_{seq}", session_id="ses_1", seq=seq, text="hi")


async def drain(flow: ScriptedFlow, clock: FakeClock, **kwargs: Any) -> list[bytes]:
    stream = sse.event_stream(cast("Any", flow), ALICE, "ses_1", clock=clock, first=[], **kwargs)
    async with asyncio.timeout(10):
        return [frame async for frame in stream]


async def test_an_idle_stream_polls_less_and_less_often() -> None:
    clock = FakeClock()
    flow = ScriptedFlow()
    await drain(
        flow,
        clock,
        poll_seconds=0.5,
        max_poll_seconds=4.0,
        keepalive_seconds=1000,
        max_seconds=60,
    )
    sleeps = clock.sleeps
    assert sleeps[0] == 0.5
    assert sleeps[:5] == pytest.approx([0.5, 0.75, 1.125, 1.6875, 2.53125])
    assert max(sleeps) <= 4.0 and 4.0 in sleeps  # reaches the ceiling and stays there
    assert sleeps[-1] <= 4.0
    assert flow.polls <= 21  # an idle minute: far fewer than the 120 polls of a fixed 0.5 s
    assert sum(sleeps) == pytest.approx(60, abs=0.01)  # the last sleep ends exactly at the limit


async def test_an_event_brings_the_fast_polling_back() -> None:
    clock = FakeClock()
    # six quiet polls, then an event, then quiet again
    flow = ScriptedFlow([[], [], [], [], [], [message(1)]])
    frames = await drain(
        flow,
        clock,
        poll_seconds=0.5,
        max_poll_seconds=4.0,
        keepalive_seconds=1000,
        max_seconds=40,
    )
    assert any(f.startswith(b"id: 1\n") for f in frames)
    sleeps = clock.sleeps
    assert sleeps[5] > 2.0  # still backed off while it was idle ...
    assert sleeps[6] == 0.5  # ... and back to the base interval right after an event
    assert sleeps[7] == 0.5
    assert sleeps[8] == pytest.approx(0.75)  # then it backs off again


async def test_without_a_max_the_poll_interval_stays_fixed() -> None:
    clock = FakeClock()
    await drain(ScriptedFlow(), clock, poll_seconds=0.5, keepalive_seconds=1000, max_seconds=3)
    assert set(clock.sleeps) == {0.5}


async def test_a_backed_off_stream_still_sends_keepalives_on_time() -> None:
    clock = FakeClock()
    frames = await drain(
        ScriptedFlow(),
        clock,
        poll_seconds=0.5,
        max_poll_seconds=30.0,
        keepalive_seconds=15,
        max_seconds=60,
    )
    assert frames.count(sse.KEEPALIVE) == 3  # at 15, 30 and 45 seconds (the stream ends at 60)
    assert max(clock.sleeps) <= 15.0  # a long back-off never sleeps past the next keepalive


async def test_the_route_passes_the_configured_backoff_and_clock_to_the_stream(
    state: AppState, api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    real = sse.event_stream

    def spy(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr("mde_context_api.routes.event_stream", spy)
    monkeypatch.setattr(state.settings, "sse_max_poll_seconds", 7.0)
    await api.create()
    assert (await api.get("/stream", params={"follow": "false"})).status_code == 200
    assert seen["max_poll_seconds"] == 7.0 and seen["clock"] is state.clock
    assert seen["poll_seconds"] == state.settings.sse_poll_seconds


# --------------------------------------------------------------------------- session rate limit


def sessions(clock: FakeClock, tmp_path: Path, **overrides: int) -> SessionRateLimiter:
    return SessionRateLimiter(clock, make_settings(tmp_path, **overrides))


def test_the_eleventh_session_in_a_minute_is_refused_until_the_oldest_leaves(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    limiter = sessions(clock, tmp_path)  # defaults: 10 per minute, 100 per hour
    for _ in range(10):
        limiter.hit(ALICE)
        clock.advance(1)
    with pytest.raises(TooManyRequests) as refused:
        limiter.hit(ALICE)
    assert refused.value.status == 429 and refused.value.code == "too_many_requests"
    # the oldest request was 10 s ago: it leaves the window in 50 s
    assert refused.value.headers["Retry-After"] == "50"
    assert "50 seconds" in refused.value.message
    clock.advance(49)
    with pytest.raises(TooManyRequests):
        limiter.hit(ALICE)
    clock.advance(1)
    limiter.hit(ALICE)  # exactly when the oldest leaves


def test_a_refused_request_is_not_counted(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = sessions(clock, tmp_path, api_session_rate_per_minute=2)
    limiter.hit(ALICE)
    limiter.hit(ALICE)
    for _ in range(50):  # hammering does not push the retry time out
        with pytest.raises(TooManyRequests) as refused:
            limiter.hit(ALICE)
        assert refused.value.headers["Retry-After"] == "60"
    clock.advance(60)
    limiter.hit(ALICE)


def test_the_hourly_window_counts_slow_clients_too(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = sessions(clock, tmp_path)
    for _ in range(100):  # 7 per minute: always inside the minute limit
        limiter.hit(ALICE)
        clock.advance(8.5)
    with pytest.raises(TooManyRequests) as refused:
        limiter.hit(ALICE)
    wait = int(refused.value.headers["Retry-After"])
    assert 1 <= wait <= 3600
    clock.advance(wait)
    limiter.hit(ALICE)


def test_callers_and_tenants_have_their_own_windows(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = sessions(
        clock,
        tmp_path,
        api_session_rate_per_minute=3,
        api_tenant_session_rate_per_minute=5,
    )
    for _ in range(3):
        limiter.hit(ALICE)
    with pytest.raises(TooManyRequests) as caller_cap:
        limiter.hit(ALICE)
    assert "organisation" not in caller_cap.value.message  # her own limit
    limiter.hit(BOB)
    limiter.hit(BOB)  # five acme sessions this minute now
    with pytest.raises(TooManyRequests) as tenant_cap:
        limiter.hit(BOB)  # bob is under his own limit; the tenant is not
    assert "organisation" in tenant_cap.value.message
    limiter.hit(ZED)  # another tenant, same user name: untouched
    clock.advance(60)
    limiter.hit(BOB)


def test_zero_turns_a_window_off(tmp_path: Path) -> None:
    limiter = sessions(
        FakeClock(),
        tmp_path,
        api_session_rate_per_minute=0,
        api_session_rate_per_hour=0,
        api_tenant_session_rate_per_minute=0,
        api_tenant_session_rate_per_hour=0,
    )
    for _ in range(500):
        limiter.hit(ALICE)


def test_idle_keys_are_swept_so_invented_callers_cannot_grow_the_table(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = sessions(clock, tmp_path)
    for n in range(3000):  # a flood of one-off callers, each in a tenant of its own
        limiter.hit(Principal(tenant_id=f"t{n}", user_id=f"user-{n}"))
        clock.advance(20)  # an hour is 180 of them
    assert len(limiter._keys) < 1_500


# --------------------------------------------------------------------------- the HTTP surface


async def test_creating_sessions_too_fast_is_a_429_with_retry_after(tmp_path: Path) -> None:
    clock = FakeClock()
    state = await build_state(
        make_settings(tmp_path / "p", api_session_rate_per_minute=3), clock=clock
    )
    try:
        async with make_client(state) as client:
            for _ in range(3):
                assert (
                    await client.post("/v1/sessions", json={}, headers=headers())
                ).status_code == 201
            limited = await client.post("/v1/sessions", json={}, headers=headers())
            assert limited.status_code == 429
            assert limited.headers["retry-after"] == "60"
            assert limited.json()["code"] == "too_many_requests"
            assert "wait 60 seconds" in limited.json()["message"]
            # another caller is fine; the same caller waits
            other = await client.post("/v1/sessions", json={}, headers=headers(user="u2"))
            assert other.status_code == 201
            clock.advance(61)
            assert (
                await client.post("/v1/sessions", json={}, headers=headers())
            ).status_code == 201
    finally:
        await state.aclose()


async def test_a_tenant_cannot_start_sessions_faster_than_its_limit(tmp_path: Path) -> None:
    clock = FakeClock()
    state = await build_state(
        make_settings(
            tmp_path / "p",
            api_session_rate_per_minute=100,
            api_tenant_session_rate_per_minute=4,
        ),
        clock=clock,
    )
    try:
        async with make_client(state) as client:
            for n in range(4):
                resp = await client.post("/v1/sessions", json={}, headers=headers(user=f"u{n}"))
                assert resp.status_code == 201
            limited = await client.post("/v1/sessions", json={}, headers=headers(user="u9"))
            assert limited.status_code == 429 and "organisation" in limited.json()["message"]
            elsewhere = await client.post(
                "/v1/sessions", json={}, headers=headers(tenant="other", user="u9")
            )
            assert elsewhere.status_code == 201
            clock.advance(timedelta(minutes=1).total_seconds())
            assert (
                await client.post("/v1/sessions", json={}, headers=headers(user="u9"))
            ).status_code == 201
    finally:
        await state.aclose()


async def test_only_session_creation_is_rate_limited(mem_api: Api, mem_state: AppState) -> None:
    mem_state.settings.api_session_rate_per_minute = 1
    mem_state.settings.api_session_rate_per_hour = 1
    await mem_api.create()
    for _ in range(30):  # reads, events and snapshots are not limited by this
        assert (await mem_api.get()).status_code == 200
    again = await mem_api.client.post("/v1/sessions", json={}, headers=mem_api.headers)
    assert again.status_code == 429


async def test_an_invalid_policy_attempt_still_counts_against_the_window(tmp_path: Path) -> None:
    """The window counts attempts that got past authentication, valid or not: a loop of bad
    requests cannot be used to probe without limit."""
    state = await build_state(
        make_settings(tmp_path / "p", api_session_rate_per_minute=2), clock=FakeClock()
    )
    try:
        async with make_client(state) as client:
            for _ in range(2):
                bad = await client.post(
                    "/v1/sessions", json={"policy_id": "nope"}, headers=headers()
                )
                assert bad.status_code == 422
            assert (
                await client.post("/v1/sessions", json={}, headers=headers())
            ).status_code == 429
    finally:
        await state.aclose()


def test_abuse_limits_bundle_uses_the_clock_it_is_given(tmp_path: Path) -> None:
    clock = FakeClock()
    limits = AbuseLimits(make_settings(tmp_path), clock)
    assert limits.clock is clock
    assert limits.streams.active(ALICE) == 0
