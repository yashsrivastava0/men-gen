"""Server-sent events over the outbound event log, with ``Last-Event-ID`` replay.

Adapted from the Context API's ``sse.py`` (never imported from it). Wire format, one frame per event::

    retry: 3000

    id: 17
    event: assistant_message
    data: {"idx":..,"direction":"out","type":"assistant_message","seq":17,"payload":{...},...}

``id`` is the per-session ``seq``; a client that reconnects with ``Last-Event-ID: 17`` (or ``?after=17``)
gets everything after 17, then the live tail. ``: keepalive`` goes out when nothing was sent for a while.
No connection is held across a sleep: each poll is one ``service.list_outbound`` call. Idle streams back
off (poll interval grows by half after every empty poll, up to ``max_poll_seconds``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Final

from mde_compass.domain.events import StoredEvent
from mde_compass.engine.ports import Caller, CompassService
from mde_core.errors import MdeError

RETRY_MS: Final = 3000
KEEPALIVE: Final = b": keepalive\n\n"
RETRY_FRAME: Final = f"retry: {RETRY_MS}\n\n".encode()
BACKOFF_FACTOR: Final = 1.5
MIN_SLEEP_SECONDS: Final = 0.001
MAX_CURSOR_DIGITS: Final = 12
MAX_CURSOR: Final = 2_147_483_647

IsDisconnected = Callable[[], Awaitable[bool]]


def format_event(event: StoredEvent) -> bytes:
    """One SSE frame. The JSON is compact, so it never contains a raw newline."""
    data = event.model_dump_json()
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n".encode()


def format_error(code: str, message: str) -> bytes:
    data = json.dumps({"code": code, "message": message}, separators=(",", ":"))
    return f"event: stream_error\ndata: {data}\n\n".encode()


def parse_last_event_id(value: str | None) -> int | None:
    """The cursor from ``Last-Event-ID``: ASCII digits, at most 12, clamped; anything else is None."""
    if value is None:
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit() or len(text) > MAX_CURSOR_DIGITS:
        return None
    return min(int(text), MAX_CURSOR)


async def _never_disconnected() -> bool:
    return False


async def event_stream(
    service: CompassService,
    caller: Caller,
    session_id: str,
    *,
    after_seq: int = 0,
    poll_seconds: float = 0.25,
    max_poll_seconds: float | None = None,
    keepalive_seconds: float = 15.0,
    max_seconds: float = 900.0,
    batch: int = 200,
    follow: bool = True,
    first: list[StoredEvent] | None = None,
    is_disconnected: IsDisconnected = _never_disconnected,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[bytes]:
    """Yield SSE frames: stored events after ``after_seq``, then (``follow``) the live tail.

    ``first`` is an already-read first batch (the route reads it before the response starts so an
    unknown session is a clean 404, not a broken stream).
    """
    ceiling = max(poll_seconds, max_poll_seconds or poll_seconds)
    delay = poll_seconds
    started = monotonic()
    last_sent = started
    cursor = after_seq
    yield RETRY_FRAME
    pending = first
    while True:
        try:
            events = (
                pending
                if pending is not None
                else await service.list_outbound(caller, session_id, after_seq=cursor, limit=batch)
            )
        except MdeError as exc:
            yield format_error(exc.code, exc.message)
            return
        pending = None
        for event in events:
            if event.seq is None or event.seq <= cursor:
                continue
            yield format_event(event)
            cursor = event.seq
            last_sent = monotonic()
        if len(events) >= batch:
            continue
        if not follow:
            return
        now = monotonic()
        if now - started >= max_seconds or await is_disconnected():
            return
        if now - last_sent >= keepalive_seconds:
            yield KEEPALIVE
            last_sent = now
        if events:
            delay = poll_seconds
        until_end = max_seconds - (now - started)
        until_keepalive = keepalive_seconds - (now - last_sent)
        await sleep(max(MIN_SLEEP_SECONDS, min(delay, until_end, until_keepalive)))
        if not events:
            delay = min(delay * BACKOFF_FACTOR, ceiling)
