"""Circuit breaker (brief 7.8): after N consecutive failures stop calling the provider for T seconds.

``CircuitOpen`` is raised BEFORE budget is reserved, so engines use their deterministic fallbacks
instead of hammering a failing API. After T seconds one probe call is allowed (half-open): success
closes the circuit, failure re-opens it for another T seconds.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from mde_core.clock import Clock
from mde_core.errors import CircuitOpen

BreakerState = Literal["closed", "open", "half_open"]


def breaker_key(route: str, tool_kinds: Iterable[str] = ()) -> str:
    """The identity of one circuit breaker: the route plus the class of tools the call uses.

    A web-search outage (a tool call) must not open the breaker that guards plain structured calls
    on the same route, and the other way round: they fail for different reasons (the search tool
    versus the model), so they are tracked separately. Concurrency and RPM limits stay per route.
    ``standard`` is the key of tool-less calls; ``standard+web_search`` of calls with that tool.
    """
    kinds = sorted(set(tool_kinds))
    return f"{route}+{'+'.join(kinds)}" if kinds else route


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int, open_seconds: float, clock: Clock) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock.monotonic() - self._opened_at >= self.open_seconds:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """Raise :class:`CircuitOpen` unless a call is allowed (closed, or the single probe)."""
        state = self.state
        if state == "closed":
            return
        if state == "half_open" and not self._probing:
            self._probing = True
            return
        remaining = 0.0
        if self._opened_at is not None:
            remaining = max(0.0, self.open_seconds - (self._clock.monotonic() - self._opened_at))
        raise CircuitOpen(
            "circuit open: the provider failed repeatedly; use the fallback",
            details={"retry_in_seconds": round(remaining, 3), "state": state},
        )

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probing = False

    def record_failure(self) -> None:
        self._probing = False
        if self._opened_at is not None:  # a failed probe re-opens for another full window
            self._opened_at = self._clock.monotonic()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock.monotonic()

    def record_neutral(self) -> None:
        """The call ended without telling us about provider health (cancelled, our own 4xx)."""
        self._probing = False
