"""Concurrency is COMPUTED from the consumer profile, not chosen (doc 15 10.3), and adapted by additive increase and
multiplicative decrease on rate-limit responses (never a silent quality drop; never a fallback to a weaker route).

The run path feeds the limiter (the model calls go through `CallRunner`, which uses `watching`):

* a rate-limit (429) response from the provider, one the gateway's own retries absorbed or one that survived them,
  halves the width once per RATE-LIMIT EVENT: all the calls that were already in flight when it hit, and all the
  retries of one call, count as the same event (`epoch`), so a burst of eight 429s is one halving, not eight;
* a call that finishes after `clean_window_seconds` without a 429 adds `additive_increase` workers, up to the width
  that was computed from the provider's limits.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Iterator
from contextvars import ContextVar

from mde_core.clock import Clock, SystemClock
from mde_spec_core.config import SpecConfig
from mde_spec_core.profiles import ConsumerProfile

RATE_LIMIT_STATUS = 429


def compute_k(
    profile: ConsumerProfile,
    avg_input_tokens: int,
    call_seconds: float,
    cfg: SpecConfig,
    *,
    tpm_share: float = 0.5,
) -> int:
    """k = max(1, floor(TPM share / (input tokens per call x calls per minute per worker))), bounded by the request and
    output limits and max_parallel. Unknown limits fall back to the configured initial width (documented, conservative)."""
    lim = profile.limits
    if not lim.input_tokens_per_minute:
        return cfg.concurrency.initial
    per_worker_in = max(1, avg_input_tokens) * (60.0 / max(1.0, call_seconds))
    k = max(1, math.floor(lim.input_tokens_per_minute * tpm_share / per_worker_in))
    if lim.requests_per_minute:
        k = min(
            k,
            max(
                1, math.floor(lim.requests_per_minute * tpm_share / (60.0 / max(1.0, call_seconds)))
            ),
        )
    if lim.max_parallel:
        k = min(k, lim.max_parallel)
    return max(1, k)


class Limiter:
    """An asyncio semaphore whose width adapts: +additive_increase after a clean window, x0.5 on a rate limit."""

    def __init__(self, k: int, cfg: SpecConfig, *, clock: Clock | None = None) -> None:
        self.cfg = cfg
        self.k_max = max(1, k)
        self.k = max(cfg.concurrency.min, min(self.k_max, cfg.concurrency.initial))
        self.epoch = (
            0  # counts the halvings: a call remembers it at its start (see `note_rate_limit`)
        )
        self._clock: Clock = clock or SystemClock()
        self._quiet_since = self._clock.monotonic()  # the last halving or increase
        self._active = 0
        self._cond = asyncio.Condition()

    async def __aenter__(self) -> Limiter:
        async with self._cond:
            while self._active >= self.k:
                await self._cond.wait()
            self._active += 1
        return self

    async def __aexit__(self, *exc: object) -> None:
        async with self._cond:
            self._active -= 1
            self._cond.notify_all()

    # ---- increase ------------------------------------------------------------------------------------------
    async def clean_window(self) -> None:
        """One clean window passed: add the configured number of workers (never above the computed width)."""
        async with self._cond:
            self.k = min(self.k_max, self.k + self.cfg.concurrency.additive_increase)
            self._quiet_since = self._clock.monotonic()
            self._cond.notify_all()

    async def call_ok(self) -> None:
        """A call finished without a rate-limit error: if a whole clean window has passed since the last change,
        that window earns one step up (additive increase)."""
        if self._clock.monotonic() - self._quiet_since >= self.cfg.concurrency.clean_window_seconds:
            await self.clean_window()

    # ---- decrease ------------------------------------------------------------------------------------------
    def note_rate_limit(self, since: int | None = None) -> bool:
        """A rate-limit response arrived: halve the width (never below the configured minimum).

        `since` is the `epoch` the reporting call saw when it started. If the width was already halved after that,
        this response belongs to the same event and changes nothing (returns False). No lock is needed: lowering the
        width wakes nobody, and the event loop runs one callback at a time."""
        if since is not None and since < self.epoch:
            return False
        self.k = max(
            self.cfg.concurrency.min,
            math.floor(self.k * self.cfg.concurrency.multiplicative_decrease),
        )
        self.epoch += 1
        self._quiet_since = self._clock.monotonic()
        return True

    async def rate_limited(self) -> None:
        self.note_rate_limit()


# ---- the hook the run path installs around a model call -------------------------------------------------------
class _Watch:
    """One model call's view of the limiter: the epoch it started in decides which 429s are new events."""

    def __init__(self, limiter: Limiter) -> None:
        self.limiter = limiter
        self.epoch = limiter.epoch

    def provider_error(self, exc: BaseException) -> None:
        if getattr(exc, "status", None) == RATE_LIMIT_STATUS:
            self.limiter.note_rate_limit(since=self.epoch)


_WATCH: ContextVar[_Watch | None] = ContextVar("mde_spec_rate_watch", default=None)


@contextlib.contextmanager
def watching(limiter: Limiter | None) -> Iterator[None]:
    """Around ONE gateway call: every 429 the gateway sees for it (absorbed by its retries or not) lowers `limiter`.
    A context variable carries the watch to the gateway's observer, so concurrent calls of different runs never
    mix and nothing is global. Without a limiter this is a no-op."""
    if limiter is None:
        yield
        return
    token = _WATCH.set(_Watch(limiter))
    try:
        yield
    finally:
        _WATCH.reset(token)


def observe_provider_error(exc: BaseException) -> None:
    """The `GatewayConfig.on_provider_error` hook of Spec: forwards to the watch of the call in progress, if any."""
    watch = _WATCH.get()
    if watch is not None:
        watch.provider_error(exc)
