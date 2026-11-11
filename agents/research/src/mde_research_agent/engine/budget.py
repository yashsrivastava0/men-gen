"""Run budget (RV-12) and failure ledger.

``RunBudget`` counts searches and pages at ADMISSION (synchronously, before the I/O), so the hard ceilings are never
exceeded however many workers race. Soft ceilings are targets the run aims at; hard ones are limits. Time comes
from an injectable clock so tests never sleep. Money is integer micro-dollars. ``FailureLedger`` counts terminal
failures by channel and reason (a timeout and a zero-token model call are failures; retries and rate-limit waits
are tracked separately).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from mde_core.clock import Clock
from mde_research_agent.catalog.models import Ceiling


@dataclass
class FailureLedger:
    by_kind: Counter[tuple[str, str]] = field(default_factory=Counter)
    by_target: Counter[str] = field(default_factory=Counter)
    retries: int = 0
    rate_limit_waits: int = 0

    def record(self, channel: str, reason: str, target_id: str | None = None) -> None:
        self.by_kind[(channel, reason)] += 1
        if target_id:
            self.by_target[target_id] += 1

    @property
    def total(self) -> int:
        return sum(self.by_kind.values())

    def count(self, channel: str, reason: str) -> int:
        return self.by_kind.get((channel, reason), 0)

    def as_list(self) -> list[dict[str, object]]:
        return [
            {"channel": c, "reason": r, "count": n} for (c, r), n in sorted(self.by_kind.items())
        ]


class RunBudget:
    def __init__(
        self, soft: Ceiling, hard: Ceiling, clock: Clock, *, run_cap_micros: int | None = None
    ) -> None:
        self.soft = soft
        self.hard = hard
        self._clock = clock
        self._t0 = clock.monotonic()
        self.searches_used = 0
        self.pages_read = 0
        self.llm_micros = 0
        self.search_micros = 0
        self.run_cap_micros = run_cap_micros
        self.on_search_cost: Callable[[int, str], None] | None = None

    # --- admission ------------------------------------------------------------------------------
    def try_search(self) -> bool:
        if self.searches_used >= self.hard.searches or self.time_up_hard() or self.over_money():
            return False
        self.searches_used += 1
        return True

    def try_page(self) -> bool:
        if self.pages_read >= self.hard.pages or self.time_up_hard() or self.over_money():
            return False
        self.pages_read += 1
        return True

    # --- money -----------------------------------------------------------------------------------
    def add_llm_cost(self, micros: int) -> None:
        self.llm_micros += max(0, micros)

    def add_search_cost(self, micros: int, provider: str) -> None:
        self.search_micros += max(0, micros)
        if self.on_search_cost is not None and micros:
            self.on_search_cost(micros, provider)

    @property
    def usd_micros(self) -> int:
        return self.llm_micros + self.search_micros

    def over_money(self) -> bool:
        return self.run_cap_micros is not None and self.usd_micros >= self.run_cap_micros

    # --- time ------------------------------------------------------------------------------------
    def elapsed(self) -> float:
        return self._clock.monotonic() - self._t0

    def time_up_soft(self) -> bool:
        return self.elapsed() >= self.soft.seconds

    def time_up_hard(self) -> bool:
        return self.elapsed() >= self.hard.seconds

    def searches_left(self) -> int:
        return max(0, self.hard.searches - self.searches_used)

    def exhausted(self) -> str | None:
        """Why no new search can start (None while there is room)."""
        if self.searches_used >= self.hard.searches:
            return "searches"
        if self.time_up_hard():
            return "time"
        if self.over_money():
            return "money"
        return None
