"""Abuse limits: concurrent event streams and session creation, per caller and per tenant.

Both live in process memory (one API process; a gateway in front is still expected for the network
edge, see the docs) and are driven by an injectable ``Clock``, so tests use a fake one.

* **Streams.** Each live (``follow``) event stream holds a *lease* for as long as it runs. The cap
  per caller is ``API_MAX_STREAMS_PER_PRINCIPAL`` (default 5); the next one is a ``429``. A lease is
  released when the stream ends for any reason (finished, client gone, error). A lease also *expires*
  on its own a little after the stream's own maximum duration, so a stream that somehow never ran to
  its ``finally`` cannot hold a slot forever.
* **Session creation.** A sliding window per caller and per tenant (defaults 10 per minute and 100
  per hour each): every live session may spend up to the session cap of the LLM budget, so a handful
  of sessions per minute is already a lot. A refused request is not recorded, and the ``429`` carries
  ``Retry-After`` (seconds until the oldest counted request leaves the window).

The caller key is ``(tenant_id, user_id)``. In dev-header mode both are self-declared, so these
limits only bound an honest or careless client there; with ``API_TOKENS`` they bind real identities.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from mde_context_api.errors import TooManyRequests, TooManyStreams
from mde_context_api.settings import ApiSettings
from mde_contracts.common import Principal
from mde_core.clock import Clock, SystemClock

STREAM_LEASE_GRACE_SECONDS = 30.0
MINUTE = 60.0
HOUR = 3600.0
_SWEEP_EVERY = 512  # sweep idle keys every this many accepted requests
_SWEEP_ABOVE = 2_048  # ... or whenever more keys than this are held

CallerKey = tuple[str, str]

STREAMS_TEXT = (
    "You already have {n} live streams open. Close one (or wait a few seconds for an old one to "
    "finish) and try again."
)
RATE_TEXT = "You are starting sessions too quickly. Please wait {wait} seconds and try again."
TENANT_RATE_TEXT = (
    "Your organisation is starting sessions too quickly right now. "
    "Please wait {wait} seconds and try again."
)


def caller_key(principal: Principal) -> CallerKey:
    return (principal.tenant_id, principal.user_id)


class StreamLease:
    """One open stream's slot. ``release`` is idempotent (a ``finally`` and a backstop may both call)."""

    def __init__(self, owner: StreamLimiter, key: CallerKey, expires_at: float) -> None:
        self._owner = owner
        self._key = key
        self.expires_at = expires_at
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._owner.forget(self._key, self)


class StreamLimiter:
    def __init__(self, clock: Clock, limit: Callable[[], int]) -> None:
        self._clock = clock
        self._limit = limit  # read per request: settings may change (and tests change them)
        self._leases: dict[CallerKey, list[StreamLease]] = {}

    def active(self, principal: Principal) -> int:
        """How many live streams this caller holds right now (expired leases do not count)."""
        return len(self._live(caller_key(principal)))

    def acquire(self, principal: Principal, *, ttl_seconds: float) -> StreamLease | None:
        """A lease for one more stream, ``None`` when the cap is off, or ``TooManyStreams``."""
        limit = self._limit()
        if limit <= 0:
            return None
        key = caller_key(principal)
        live = self._live(key)
        if len(live) >= limit:
            raise TooManyStreams(STREAMS_TEXT.format(n=len(live)))
        lease = StreamLease(
            self, key, self._clock.monotonic() + ttl_seconds + STREAM_LEASE_GRACE_SECONDS
        )
        live.append(lease)
        self._leases[key] = live
        return lease

    def forget(self, key: CallerKey, lease: StreamLease) -> None:
        held = self._leases.get(key)
        if held is None:
            return
        if lease in held:
            held.remove(lease)
        if not held:
            del self._leases[key]

    def _live(self, key: CallerKey) -> list[StreamLease]:
        now = self._clock.monotonic()
        held = [lease for lease in self._leases.get(key, []) if lease.expires_at > now]
        if held:
            self._leases[key] = held
        else:
            self._leases.pop(key, None)
        return held


@dataclass(frozen=True)
class _Window:
    seconds: float
    limit: int


@dataclass
class _Key:
    stamps: deque[float] = field(default_factory=deque[float])


class SessionRateLimiter:
    """Sliding-window log per caller and per tenant (``0`` disables a window)."""

    def __init__(self, clock: Clock, settings: ApiSettings) -> None:
        self._clock = clock
        self._settings = settings  # read per request
        self._keys: dict[tuple[str, str, str], _Key] = {}
        self._accepted = 0

    def _windows(self, tenant_level: bool) -> list[_Window]:
        s = self._settings
        if tenant_level:
            pairs = (
                (MINUTE, s.api_tenant_session_rate_per_minute),
                (HOUR, s.api_tenant_session_rate_per_hour),
            )
        else:
            pairs = ((MINUTE, s.api_session_rate_per_minute), (HOUR, s.api_session_rate_per_hour))
        return [_Window(seconds, limit) for seconds, limit in pairs if limit > 0]

    def hit(self, principal: Principal) -> None:
        """Count one session creation, or raise ``TooManyRequests`` (and count nothing)."""
        now = self._clock.monotonic()
        checks = (
            (("caller", principal.tenant_id, principal.user_id), self._windows(False), False),
            (("tenant", principal.tenant_id, ""), self._windows(True), True),
        )
        worst = 0.0
        tenant_hit = False
        for key, windows, tenant_level in checks:
            wait = self._wait(key, windows, now)
            if wait > 0:
                worst = max(worst, wait)
                tenant_hit = tenant_hit or tenant_level
        if worst > 0:
            seconds = max(1, math.ceil(worst))
            template = TENANT_RATE_TEXT if tenant_hit else RATE_TEXT
            raise TooManyRequests(template.format(wait=seconds), retry_after=seconds)
        for key, windows, _ in checks:
            if windows:
                stamps = self._keys.setdefault(key, _Key()).stamps
                stamps.append(now)
                while stamps[0] <= now - HOUR:  # nothing older than the longest window is needed
                    stamps.popleft()
        self._accepted += 1
        if self._accepted % _SWEEP_EVERY == 0 or len(self._keys) > _SWEEP_ABOVE:
            self._sweep(now)

    def _wait(self, key: tuple[str, str, str], windows: list[_Window], now: float) -> float:
        """Seconds until one more request would be allowed under every window (0 = allowed now)."""
        held = self._keys.get(key)
        if held is None or not windows:
            return 0.0
        wait = 0.0
        for window in windows:
            horizon = now - window.seconds
            inside = [t for t in held.stamps if t > horizon]
            if len(inside) >= window.limit:
                # the (count - limit + 1)th oldest request must leave the window first
                leaves = inside[len(inside) - window.limit] + window.seconds
                wait = max(wait, leaves - now)
        return wait

    def _sweep(self, now: float) -> None:
        horizon = now - HOUR
        for key in [k for k, v in self._keys.items() if not v.stamps or v.stamps[-1] <= horizon]:
            del self._keys[key]
        for held in self._keys.values():
            while held.stamps and held.stamps[0] <= horizon:
                held.stamps.popleft()


class AbuseLimits:
    """The limiters one API process holds (``AppState.limits``)."""

    def __init__(self, settings: ApiSettings, clock: Clock | None = None) -> None:
        the_clock = clock or SystemClock()
        self.clock = the_clock
        self.streams = StreamLimiter(the_clock, lambda: settings.api_max_streams_per_principal)
        self.sessions = SessionRateLimiter(the_clock, settings)
