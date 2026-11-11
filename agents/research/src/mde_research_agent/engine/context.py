"""The shared context of one run: ports, state, budget, events, locks. Plain data plus a few helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mde_core.cancel import CancellationToken
from mde_core.clock import Clock
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.events import Counters
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Plan
from mde_research_agent.engine.budget import FailureLedger, RunBudget
from mde_research_agent.engine.state import (
    AtomRecord,
    DropCounts,
    EntityRecord,
    PageRecord,
    QueryRecord,
    TargetState,
)
from mde_research_agent.events.writer import EventWriter
from mde_research_agent.ids import Ids
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort

SEARCH_TIMEOUT_S = 8.0
FETCH_TIMEOUT_S = 12.0
SEARCH_TRANSPORT_ATTEMPTS = 3
RATE_LIMIT_WAIT_CAP_S = 20.0
PASSAGE_CHARS = {"focused": 6000, "standard": 7500, "deep": 9000}


@dataclass
class RunContext:
    run_id: str
    input: ResearchInput
    catalog: Catalog
    plan: Plan
    search: SearchPort
    fetch: FetchPort
    runner: CallRunner
    events: EventWriter
    budget: RunBudget
    failures: FailureLedger
    clock: Clock
    ids: Ids
    as_of: date
    run_cancel: CancellationToken
    lanes_search: int = 6
    lanes_extract: int = 8
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    targets: dict[str, TargetState] = field(default_factory=dict)
    pages: dict[str, PageRecord] = field(default_factory=dict)
    pages_by_id: dict[str, PageRecord] = field(default_factory=dict)
    page_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    queries: list[QueryRecord] = field(default_factory=list)
    entities: dict[str, EntityRecord] = field(default_factory=dict)
    drops: DropCounts = field(default_factory=DropCounts)
    check_inflight: dict[str, Any] = field(default_factory=dict)  # pair key -> future of the checker's verdict
    provider_down: bool = False
    budget_stopped: bool = False
    no_results: bool = False  # round 1 found nothing for any topic
    drain: bool = False  # no new searches or fetches: finish what is read, then assemble
    round: int = 0
    warned: set[str] = field(default_factory=set)
    checker_vendor: str = "none"
    search_sem: asyncio.Semaphore = field(init=False)
    fetch_sem: asyncio.Semaphore = field(init=False)
    llm_sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.search_sem = asyncio.Semaphore(max(1, self.lanes_search))
        self.fetch_sem = asyncio.Semaphore(max(1, self.lanes_search))
        self.llm_sem = asyncio.Semaphore(max(1, self.lanes_extract))

    # --- counters -----------------------------------------------------------------------------------
    def count_drop(self, reason: str) -> None:
        """Count one dropped item under its reason (an unknown reason is counted as ``invalid``)."""
        name = reason if hasattr(self.drops, reason) and reason != "unchecked_high_impact" else "invalid"
        setattr(self.drops, name, getattr(self.drops, name) + 1)

    def all_atoms(self) -> list[AtomRecord]:
        return [a for ts in self.targets.values() for a in ts.atoms]

    def verified_count(self) -> int:
        return sum(1 for a in self.all_atoms() if a.status == "verified")

    def counters(self) -> Counters:
        done = sum(
            1
            for ts in self.targets.values()
            if ts.status in ("covered", "not_found", "cancelled", "skipped", "partial")
        )
        elapsed = int(self.budget.elapsed())
        planned = min(
            self.budget.soft.searches, max(self.budget.searches_used, len(self.targets) * 2)
        )
        eta = None
        if done and elapsed:
            remaining = max(0, len(self.targets) - done)
            eta = int(elapsed / done * remaining) if remaining else 0
        return Counters(
            searches_used=self.budget.searches_used,
            searches_planned=planned,
            searches_ceiling=self.budget.hard.searches,
            pages_read=self.budget.pages_read,
            facts_verified=self.verified_count(),
            targets_done=done,
            targets_total=len(self.targets),
            elapsed_s=elapsed,
            eta_s=eta,
        )

    def active(self) -> bool:
        return not (self.run_cancel.cancelled or self.drain or self.budget_stopped)

    async def warn(self, code: str, *, once: bool = True, target_id: str | None = None) -> None:
        if once and code in self.warned:
            return
        self.warned.add(code)
        await self.events.emit(
            "warning",
            target_id=target_id,
            detail={"code": code, "message": self.events.labels.warning(code)},
            label=self.events.labels.warning(code),
        )
