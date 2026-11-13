"""Server-sent events with ``Last-Event-ID`` resume: replay stored events, then tail until a terminal event.

Frames: ``retry: 3000`` first, ``id: <seq>`` / ``event: <type>`` / ``data: <Event JSON>``, and ``: keepalive`` comments
while waiting. The generator reads only the store, so it works the same for a live run and for one finished long ago.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable

from mde_research_agent.domain.events import TERMINAL_EVENTS, Event
from mde_research_agent.store.ports import Store

KEEPALIVE_S = 15.0
MAX_STREAM_S = 3600.0
BATCH = 200


def frame(event: Event) -> str:
    data = event.model_dump_json(by_alias=True)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


async def event_stream(
    store: Store,
    run_id: str,
    after: int,
    *,
    is_active: Callable[[], Awaitable[bool]],
    keepalive_s: float = KEEPALIVE_S,
    max_seconds: float = MAX_STREAM_S,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    started = time.monotonic()
    cursor = after
    while time.monotonic() - started < max_seconds:
        if disconnected is not None and await disconnected():
            return
        events = await store.events_after(run_id, cursor, limit=BATCH)
        for e in events:
            yield frame(e)
            cursor = e.seq
            if e.type in TERMINAL_EVENTS:
                return
        if events:
            continue
        if not await is_active():
            tail = await store.events_after(run_id, cursor, limit=BATCH)
            if not tail:
                return
            continue
        await store.wait_for_events(run_id, cursor, keepalive_s)
        if not await store.events_after(run_id, cursor, limit=1):
            yield ": keepalive\n\n"
