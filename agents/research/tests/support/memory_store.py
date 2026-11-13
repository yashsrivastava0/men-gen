"""The in-memory store: dev server, ``--memory`` runs and tests. Same behaviour as PostgreSQL (one contract suite)."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from mde_core.errors import NotFound
from mde_research_agent.domain.events import Event
from mde_research_agent.store.ports import ACTIVE_STATUSES, Decision, RunActive, RunRow


class InMemoryStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRow] = {}
        self._events: dict[str, list[Event]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._evidence: dict[str, dict[str, Any]] = {}
        self._decisions: dict[str, list[Decision]] = {}
        self._cond = asyncio.Condition()

    # --- runs ------------------------------------------------------------------------------------------
    async def create_run(self, row: RunRow) -> None:
        for r in self._runs.values():
            if (
                r.tenant_id == row.tenant_id
                and r.program_id == row.program_id
                and r.status in ACTIVE_STATUSES
            ):
                raise RunActive(r.run_id)
        self._runs[row.run_id] = row.model_copy(deep=True)
        self._events.setdefault(row.run_id, [])

    async def update_run(self, tenant_id: str, run_id: str, **fields: Any) -> None:
        row = self._runs.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            raise NotFound(f"run {run_id}")
        new = row.model_copy(update=fields)
        if new.status in ACTIVE_STATUSES and row.status not in ACTIVE_STATUSES:
            for other in self._runs.values():
                if (
                    other.run_id != run_id
                    and other.tenant_id == tenant_id
                    and other.program_id == row.program_id
                    and other.status in ACTIVE_STATUSES
                ):
                    raise RunActive(other.run_id)
        self._runs[run_id] = new

    async def get_run(self, tenant_id: str, run_id: str) -> RunRow | None:
        row = self._runs.get(run_id)
        return row.model_copy(deep=True) if row is not None and row.tenant_id == tenant_id else None

    async def active_run(self, tenant_id: str, program_id: str) -> RunRow | None:
        for r in sorted(self._runs.values(), key=lambda x: x.created_at, reverse=True):
            if (
                r.tenant_id == tenant_id
                and r.program_id == program_id
                and r.status in ACTIVE_STATUSES
            ):
                return r.model_copy(deep=True)
        return None

    async def latest_committed(self, tenant_id: str, program_id: str) -> RunRow | None:
        rows = [
            r
            for r in self._runs.values()
            if r.tenant_id == tenant_id and r.program_id == program_id and r.run_id in self._results
        ]
        rows.sort(key=lambda x: (x.finished_at or x.created_at, x.run_id), reverse=True)
        return rows[0].model_copy(deep=True) if rows else None

    async def list_runs(self, tenant_id: str, limit: int = 200) -> list[RunRow]:
        rows = [r for r in self._runs.values() if r.tenant_id == tenant_id]
        rows.sort(key=lambda x: (x.created_at, x.run_id), reverse=True)
        return [r.model_copy(deep=True) for r in rows[:limit]]

    async def active_runs(self) -> list[RunRow]:
        rows = [r for r in self._runs.values() if r.status in ACTIVE_STATUSES]
        rows.sort(key=lambda x: (x.created_at, x.run_id))
        return [r.model_copy(deep=True) for r in rows]

    async def mark_interrupted(self) -> int:
        n = 0
        for rid, r in list(self._runs.items()):
            if r.status in ACTIVE_STATUSES:
                self._runs[rid] = r.model_copy(
                    update={"status": "interrupted", "reason": "server restarted"}
                )
                n += 1
        return n

    # --- events ------------------------------------------------------------------------------------------
    async def append_event(self, event: Event) -> None:
        async with self._cond:
            log = self._events.setdefault(event.run_id, [])
            if log and event.seq != log[-1].seq + 1:
                raise ValueError(f"events must be contiguous: got {event.seq} after {log[-1].seq}")
            if not log and event.seq != 1:
                raise ValueError(f"the first event must have seq 1, got {event.seq}")
            log.append(event)
            self._cond.notify_all()

    async def events_after(self, run_id: str, seq: int, limit: int = 500) -> list[Event]:
        return [e for e in self._events.get(run_id, []) if e.seq > seq][:limit]

    async def last_seq(self, run_id: str) -> int:
        log = self._events.get(run_id, [])
        return log[-1].seq if log else 0

    async def wait_for_events(self, run_id: str, after_seq: int, timeout: float) -> None:  # noqa: ASYNC109
        async def _wait() -> None:
            async with self._cond:
                await self._cond.wait_for(
                    lambda: self._events.get(run_id) and self._events[run_id][-1].seq > after_seq
                )

        try:
            await asyncio.wait_for(_wait(), timeout=timeout)
        except TimeoutError:
            return

    # --- results -----------------------------------------------------------------------------------------------
    async def save_result(
        self, run_id: str, research: dict[str, Any], evidence: dict[str, Any], content_hash: str
    ) -> None:
        if run_id in self._results:
            raise ValueError("a committed result is immutable")
        self._results[run_id] = copy.deepcopy(research)
        self._evidence[run_id] = copy.deepcopy(evidence)
        row = self._runs[run_id]
        self._runs[run_id] = row.model_copy(update={"content_hash": content_hash})

    async def get_result(self, run_id: str) -> dict[str, Any] | None:
        r = self._results.get(run_id)
        return copy.deepcopy(r) if r is not None else None

    async def get_evidence(self, run_id: str) -> dict[str, Any] | None:
        r = self._evidence.get(run_id)
        return copy.deepcopy(r) if r is not None else None

    # --- decisions ----------------------------------------------------------------------------------------------
    async def add_decision(self, decision: Decision) -> None:
        self._decisions.setdefault(decision.run_id, []).append(decision)

    async def list_decisions(self, run_id: str) -> list[Decision]:
        return list(self._decisions.get(run_id, []))

    async def aclose(self) -> None:
        return None
