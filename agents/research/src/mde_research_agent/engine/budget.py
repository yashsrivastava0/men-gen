"""Run budget (RV-12) and failure ledger.

``RunBudget`` counts searches and pages at ADMISSION (synchronously, before the I/O), so the hard ceilings are never
exceeded however many workers race. Soft ceilings are targets the run aims at; hard ones are limits. Time comes
from an injectable clock so tests never sleep. Money is integer micro-dollars. ``FailureLedger`` counts terminal
failures by channel and reason (a timeout and a zero-token model call are failures; retries and rate-limit waits
are tracked separately).

A page is a page the run actually READ. A page that could not be read (a robots refusal, no text, a wall, a timeout)
is refunded with ``refund_page``: it spent no model money and holds no evidence, so it must not use up the ceiling that
the topics still waiting for their first page need (the Small run of 2026-11-11 lost its payments topic and all of
round 2 to seven refused pages). Attempts are bounded separately, at ``PAGE_ATTEMPT_FACTOR`` times the ceiling, so a
refund can never make the loop endless.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from mde_core.clock import Clock
from mde_research_agent.catalog.models import Ceiling, HardLimits


@dataclass
class FailureLedger:
    by_kind: Counter[tuple[str, str]] = field(default_factory=Counter)
    by_target: Counter[str] = field(default_factory=Counter)
    retries: int = 0
    rate_limit_waits: int = 0
    private_blocked: int = (
        0  # queries refused for naming something private (core/privacy.py); not a failure
    )

    def count_private(self) -> None:
        self.private_blocked += 1

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
        rows: list[dict[str, object]] = [
            {"channel": c, "reason": r, "count": n} for (c, r), n in sorted(self.by_kind.items())
        ]
        if self.private_blocked:
            rows.append(
                {"channel": "query", "reason": "private_term", "count": self.private_blocked}
            )
        return rows


PAGE_ATTEMPT_FACTOR = 1.5


class RunBudget:
    def __init__(
        self, soft: Ceiling, hard: HardLimits, clock: Clock, *, run_cap_micros: int | None = None
    ) -> None:
        self.soft = soft
        self.hard = hard
        self._clock = clock
        self._t0 = clock.monotonic()
        self.searches_used = 0
        # Extra queries sent to the second engine on both-engine topics. They are NOT admitted against the search ceiling
        # (the pair counts as one search, the one the first engine's admission already counted) but their cost is booked.
        self.second_searches = 0
        self.pages_read = 0  # pages admitted and not refunded: what counts against the hard ceiling
        self.page_attempts = 0  # every admission, refunded or not
        self.llm_micros = 0
        self.search_micros = 0
        self.run_cap_micros = run_cap_micros
        self.on_search_cost: Callable[[int, str], None] | None = None
        # Every cost as it happens: ``(step, micros)``. The service books it into the spend record at once, so a
        # crash cannot lose spend. ``by_step`` is the same split for the run's metrics page.
        self.on_cost: Callable[[str, int], None] | None = None
        self.by_step: Counter[str] = Counter()

    # --- admission ------------------------------------------------------------------------------
    def try_search(self) -> bool:
        if self.searches_used >= self.hard.searches or self.time_up_hard() or self.over_money():
            return False
        self.searches_used += 1
        return True

    def try_page(self) -> bool:
        if self.pages_exhausted() or self.time_up_hard() or self.over_money():
            return False
        self.pages_read += 1
        self.page_attempts += 1
        return True

    def refund_page(self) -> None:
        """A page admitted by ``try_page`` that could not be read: it does not count against the ceiling."""
        self.pages_read = max(0, self.pages_read - 1)

    def pages_exhausted(self) -> bool:
        return self.pages_read >= self.hard.pages or self.page_attempts >= int(
            self.hard.pages * PAGE_ATTEMPT_FACTOR
        )

    def pages_left(self) -> int:
        return max(0, self.hard.pages - self.pages_read)

    # --- money -----------------------------------------------------------------------------------
    def add_llm_cost(self, micros: int, step: str = "other") -> None:
        micros = max(0, micros)
        self.llm_micros += micros
        self._booked(step, micros)

    def add_second_search_cost(self, micros: int, provider: str) -> None:
        """Book the cost of the second engine's query of a both-engine pair (counted once as a search, paid twice)."""
        self.second_searches += 1
        self.add_search_cost(micros, provider)

    def add_search_cost(self, micros: int, provider: str) -> None:
        micros = max(0, micros)
        self.search_micros += micros
        if self.on_search_cost is not None and micros:
            self.on_search_cost(micros, provider)
        self._booked(f"search.{provider}", micros)

    def _booked(self, step: str, micros: int) -> None:
        if not micros:
            return
        self.by_step[step] += micros
        if self.on_cost is not None:
            self.on_cost(step, micros)

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
        """Why no new search can start (None while there is room). Pages are not part of it: a run whose pages are
        used up still finishes what it holds; ``pages_exhausted`` is what the director asks before paying for a round."""
        if self.searches_used >= self.hard.searches:
            return "searches"
        if self.time_up_hard():
            return "time"
        if self.over_money():
            return "money"
        return None
