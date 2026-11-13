"""Concurrency is COMPUTED from the consumer profile, not chosen (doc 15 10.3), and adapted by additive increase and
multiplicative decrease on rate-limit responses (never a silent quality drop; never a fallback to a weaker route)."""

from __future__ import annotations

import asyncio
import math

from mde_spec_core.config import SpecConfig
from mde_spec_core.profiles import ConsumerProfile


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

    def __init__(self, k: int, cfg: SpecConfig) -> None:
        self.cfg = cfg
        self.k_max = max(1, k)
        self.k = max(cfg.concurrency.min, min(self.k_max, cfg.concurrency.initial))
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

    async def clean_window(self) -> None:
        async with self._cond:
            self.k = min(self.k_max, self.k + self.cfg.concurrency.additive_increase)
            self._cond.notify_all()

    async def rate_limited(self) -> None:
        async with self._cond:
            self.k = max(
                self.cfg.concurrency.min,
                math.floor(self.k * self.cfg.concurrency.multiplicative_decrease),
            )
