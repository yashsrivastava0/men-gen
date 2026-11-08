"""Server-sent events for a session's outbound events, with ``Last-Event-ID`` replay.

Wire format (one frame per outbound event)::

    id: 17
    event: assistant_message
    data: {"type":"assistant_message","seq":17,...}

``id`` is the event's per-session ``seq`` (strictly increasing), so a client that reconnects with
``Last-Event-ID: 17`` (or ``?after=17``) gets everything after 17, then the live tail. A stream starts
with ``retry: 3000``; a comment line ``: keepalive`` goes out whenever nothing was sent for a while so
proxies keep the connection open.

**No connection is held across the stream.** Each poll is one short unit of work
(``FlowService.list_events``: tenant-bound, read, closed); between polls the generator only sleeps.
A stream therefore costs the database nothing while it is idle, however many clients are attached
(D-042, D-049).

**Idle streams back off.** The poll interval starts at ``poll_seconds``, grows by half after every
empty poll up to ``max_poll_seconds`` and returns to ``poll_seconds`` as soon as an event is sent, so
many quiet streams make few queries. A sleep never overshoots the stream's end or its next keepalive.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Final

from mde_contracts.common import Principal
from mde_contracts.events import OutboundEvent
from mde_core.clock import Clock, SystemClock
from mde_core.errors import MdeError
from mde_core.logging import get_logger
from mde_flow.service import FlowService

log = get_logger("mde_context_api.sse")

RETRY_MS: Final = 3000
KEEPALIVE: Final = b": keepalive\n\n"
RETRY_FRAME: Final = f"retry: {RETRY_MS}\n\n".encode()

BACKOFF_FACTOR: Final = 1.5
MIN_SLEEP_SECONDS: Final = 0.001
MAX_CURSOR_DIGITS: Final = 12
MAX_CURSOR: Final = 2_147_483_647  # the largest seq any store column holds

IsDisconnected = Callable[[], Awaitable[bool]]


def format_event(event: OutboundEvent) -> bytes:
    """One SSE frame. The JSON is compact, so it never contains a raw newline."""
    data = event.model_dump_json()
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n".encode()


def format_error(code: str, message: str) -> bytes:
    data = json.dumps({"code": code, "message": message}, separators=(",", ":"))
    return f"event: stream_error\ndata: {data}\n\n".encode()


def parse_last_event_id(value: str | None) -> int | None:
    """The cursor from a ``Last-Event-ID`` header; anything unusable means "from the start".

    Usable means ASCII digits only and at most 12 of them (a longer value is junk, and converting
    thousands of digits is work an anonymous header should not buy); the number is clamped to the
    range a stored seq can have. Never raises.
    """
    if value is None:
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit() or len(text) > MAX_CURSOR_DIGITS:
        return None
    return min(int(text), MAX_CURSOR)


async def _never_disconnected() -> bool:
    return False


async def event_stream(
    flow: FlowService,
    principal: Principal,
    session_id: str,
    *,
    after_seq: int = 0,
    poll_seconds: float = 0.5,
    max_poll_seconds: float | None = None,
    keepalive_seconds: float = 15.0,
    max_seconds: float = 900.0,
    batch: int = 200,
    follow: bool = True,
    limit: int | None = None,
    first: list[OutboundEvent] | None = None,
    is_disconnected: IsDisconnected = _never_disconnected,
    clock: Clock | None = None,
) -> AsyncGenerator[bytes]:
    """Yield SSE frames: the stored events after ``after_seq``, then (``follow``) the live tail.

    The stream ends when ``limit`` events were sent, ``max_seconds`` passed (the client reconnects
    with ``Last-Event-ID``), the client left, or (not ``follow``) the stored events ran out.
    ``first`` is an already-read first batch (the route reads it before the response starts, so a
    missing or foreign session is a clean 404 and not a broken stream).

    ``max_poll_seconds`` (default: ``poll_seconds``, no backoff) is how long an idle stream may go
    between polls. ``clock`` supplies the time and the sleeping (tests pass a fake one).
    """
    the_clock = clock or SystemClock()
    ceiling = max(poll_seconds, max_poll_seconds or poll_seconds)
    delay = poll_seconds
    started = the_clock.monotonic()
    last_sent = started
    cursor = after_seq
    sent = 0
    yield RETRY_FRAME
    pending = first
    while True:
        try:
            events = (
                pending
                if pending is not None
                else await flow.list_events(principal, session_id, after_seq=cursor, limit=batch)
            )
        except MdeError as exc:  # the session vanished or access changed mid-stream
            yield format_error(exc.code, exc.message)
            return
        pending = None
        for event in events:
            yield format_event(event)
            cursor = max(cursor, event.seq)
            sent += 1
            last_sent = the_clock.monotonic()
            if limit is not None and sent >= limit:
                return
        if len(events) >= batch:
            continue  # more may be stored: drain before sleeping
        if not follow:
            return
        now = the_clock.monotonic()
        if now - started >= max_seconds or await is_disconnected():
            return
        if now - last_sent >= keepalive_seconds:
            yield KEEPALIVE
            last_sent = now
        if events:
            delay = poll_seconds  # something is happening: look often again
        until_end = max_seconds - (now - started)
        until_keepalive = keepalive_seconds - (now - last_sent)
        await the_clock.sleep(max(MIN_SLEEP_SECONDS, min(delay, until_end, until_keepalive)))
        if not events:
            delay = min(delay * BACKOFF_FACTOR, ceiling)  # quiet: look a little less often
