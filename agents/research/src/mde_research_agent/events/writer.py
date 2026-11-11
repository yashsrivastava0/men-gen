"""The event writer: builds, numbers, stores and fans out ``research.event.v1`` events for ONE run.

Labels come from templates, ``detail`` is cut to the per-type allowlist (RV-17: no page text, no quotes), ``seq`` is
contiguous per run, page events are coalesced when they burst (never dropped silently: ``page.reading`` is skipped
first, ``page.read`` and ``page.skipped`` always go out), and every event is appended to the store before listeners
are woken, so a reload replays exactly what was shown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from mde_core.clock import Clock
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.events import Counters, Event, clamp_detail
from mde_research_agent.events.labels import LabelBook
from mde_research_agent.ids import Ids

PAGE_BURST_PER_SECOND = 8


class EventStore(Protocol):
    async def append_event(self, event: Event) -> None: ...


class MemoryEventStore:
    """Events in a list, with a condition that wakes SSE tailers."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.cond = asyncio.Condition()

    async def append_event(self, event: Event) -> None:
        async with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def after(self, seq: int) -> list[Event]:
        return [e for e in self.events if e.seq > seq]


class EventWriter:
    def __init__(
        self,
        run_id: str,
        store: EventStore,
        catalog: Catalog,
        *,
        clock: Clock,
        ids: Ids,
        counters: Callable[[], Counters],
        language: str = "en",
    ) -> None:
        self.run_id = run_id
        self._store = store
        self._clock = clock
        self._ids = ids
        self._counters = counters
        self.labels = LabelBook(catalog, language)
        self.seq = 0
        self._page_window: list[float] = []
        self.listeners: list[Callable[[Event], None]] = []

    def _burst(self) -> bool:
        now = self._clock.monotonic()
        self._page_window = [t for t in self._page_window if now - t < 1.0]
        if len(self._page_window) >= PAGE_BURST_PER_SECOND:
            return True
        self._page_window.append(now)
        return False

    async def emit(
        self,
        type_: str,
        *,
        target_id: str | None = None,
        round_: int | None = None,
        phase: str | None = None,
        params: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> Event | None:
        if type_ == "page.reading" and self._burst():
            return None
        self.seq += 1
        merged = {**(params or {}), **(detail or {})}
        event = Event.model_validate(
            {
                "id": self._ids.new("evt"),
                "seq": self.seq,
                "ts": self._clock.now(),
                "run_id": self.run_id,
                "type": type_,
                "target_id": target_id,
                "round": round_,
                "phase": phase,
                "label": label if label is not None else self.labels.event(type_, merged),
                "detail": clamp_detail(type_, detail or {}),
                "counters": self._counters().model_dump(),
            }
        )
        await self._store.append_event(event)
        for fn in list(self.listeners):
            fn(event)
        return event
