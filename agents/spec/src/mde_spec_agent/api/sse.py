"""Server-sent events with ``Last-Event-ID`` resume: replay stored events, then tail until a terminal event.

Frames: ``retry: 3000`` first, ``id: <seq>`` / ``event: <type>`` / ``data: <Event JSON>``, and ``: keepalive`` comments
while waiting. The generator reads only the run store, so it works the same for a live run and for one finished long ago.
A run that waits at a gate stays active: the stream stays open (keepalives) until the run ends or the client leaves.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable

from mde_spec_agent.run.events import TERMINAL, Event
from mde_spec_agent.run.store import RunStore

KEEPALIVE_S = 15.0
MAX_STREAM_S = 3600.0
POLL_S = 0.25
BATCH = 200


def frame(event: Event) -> str:
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def event_stream(
    store: RunStore,
    run_id: str,
    after: int,
    *,
    is_active: Callable[[], bool],
    keepalive_s: float = KEEPALIVE_S,
    max_seconds: float = MAX_STREAM_S,
    poll_s: float = POLL_S,
    disconnected: Callable[[], object] | None = None,
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    started = time.monotonic()
    last_sent = started
    cursor = after
    while time.monotonic() - started < max_seconds:
        if disconnected is not None:
            gone = disconnected()
            if asyncio.iscoroutine(gone):
                gone = await gone
            if gone:
                return
        events = await asyncio.to_thread(store.events, run_id, cursor)
        for e in events[:BATCH]:
            yield frame(e)
            cursor = e.seq
            last_sent = time.monotonic()
            if e.type in TERMINAL:
                return
        if events:
            continue
        if not is_active():
            tail = await asyncio.to_thread(store.events, run_id, cursor)
            if not tail:
                return
            continue
        await asyncio.sleep(poll_s)
        if time.monotonic() - last_sent >= keepalive_s:
            last_sent = time.monotonic()
            yield ": keepalive\n\n"
