"""Per-route concurrency semaphore and requests-per-minute token bucket (brief 7.8).

Defaults are modest on purpose (Luna's published limits are far higher). Both use the injected
clock, so tests drive them with ``FakeClock`` and never sleep on the wall clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager

from mde_core.clock import Clock

DEFAULT_CONCURRENCY: Mapping[str, int] = {"fast": 8, "standard": 8, "deep": 2}
DEFAULT_RPM: Mapping[str, int] = {"fast": 120, "standard": 120, "deep": 30}


_EPSILON = 1e-9
_MIN_WAIT = 1e-6


class TokenBucket:
    """``rpm`` requests per minute with a burst of ``burst`` requests."""

    def __init__(self, rpm: int, *, burst: int | None = None, clock: Clock) -> None:
        if rpm < 1:
            raise ValueError("rpm must be >= 1")
        self.rate = rpm / 60.0  # tokens per second
        self.capacity = float(burst if burst is not None else max(1, min(rpm, 10)))
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    async def acquire(self) -> float:
        """Wait (on the injected clock) until a request may start. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            self._refill()
            # The tolerance matters under a fake clock: float residue such as 0.9999999999999999 tokens would give a
            # wait too small to move the clock, and the loop would never end.
            while self._tokens < 1.0 - _EPSILON:
                wait = max((1.0 - self._tokens) / self.rate, _MIN_WAIT)
                await self._clock.sleep(wait)
                waited += wait
                self._refill()
            self._tokens = max(0.0, self._tokens - 1.0)
        return waited


class RouteLimits:
    """One semaphore and one bucket per route (``fast`` / ``standard`` / ``deep``)."""

    def __init__(
        self,
        *,
        clock: Clock,
        concurrency: Mapping[str, int] | None = None,
        rpm: Mapping[str, int] | None = None,
    ) -> None:
        self._clock = clock
        self._concurrency = dict(concurrency or DEFAULT_CONCURRENCY)
        self._rpm = dict(rpm or DEFAULT_RPM)
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._buckets: dict[str, TokenBucket] = {}

    def _sem(self, route: str) -> asyncio.Semaphore:
        if route not in self._sems:
            self._sems[route] = asyncio.Semaphore(self._concurrency.get(route, 4))
        return self._sems[route]

    def _bucket(self, route: str) -> TokenBucket:
        if route not in self._buckets:
            self._buckets[route] = TokenBucket(self._rpm.get(route, 60), clock=self._clock)
        return self._buckets[route]

    @asynccontextmanager
    async def slot(self, route: str) -> AsyncGenerator[None]:
        """Hold a concurrency slot for the route and spend one rate-limit token first."""
        async with self._sem(route):
            await self._bucket(route).acquire()
            yield
