"""Retry policy (brief 7.8): exponential backoff with jitter, honoring ``Retry-After``.

Only 429, 5xx, timeouts and connection errors are retryable (the provider marks them
``ProviderError.retryable``); a 4xx validation error is never retried. Attempts and total elapsed
time are both bounded. Delays are seconds (floats are fine here: only money is exact).
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4  # total tries, including the first
    base_delay: float = 0.5
    max_delay: float = 20.0
    multiplier: float = 2.0
    jitter: float = 0.25  # +/- fraction of the backoff
    max_total_seconds: float = 60.0  # give up when elapsed + next delay would exceed this

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if not (0 <= self.jitter < 1):
            raise ValueError("jitter must be in [0, 1)")

    def backoff(self, failed_attempt: int) -> float:
        """Un-jittered delay after the ``failed_attempt``-th failure (1-based)."""
        return min(self.max_delay, self.base_delay * self.multiplier ** (failed_attempt - 1))

    def delay(
        self, failed_attempt: int, *, retry_after: float | None = None, rng: random.Random
    ) -> float:
        """Seconds to wait before the next attempt; never less than ``Retry-After``."""
        base = self.backoff(failed_attempt)
        jittered = base * (1 + rng.uniform(-self.jitter, self.jitter))
        delay = max(0.0, jittered)
        if retry_after is not None and retry_after > delay:
            delay = retry_after
        return delay
