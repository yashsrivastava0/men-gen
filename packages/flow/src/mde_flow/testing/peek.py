"""Store-agnostic inspection for tests: the same assertions over ``InMemoryStore`` and Postgres.

``TrackingStore`` wraps any ``Store`` and remembers which sessions were created and which jobs were
enqueued through it (the repository ports have no "list jobs" or "list sessions"). ``Peek`` reads
them back through the ordinary repositories, so a scenario test written against it runs unchanged
on the in-memory fake and on ``PostgresStore``. A job or session whose creating unit of work rolled
back is simply not found and is skipped.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from mde_contracts.events import OutboundEvent
from mde_contracts.gates import ApprovalRecord, PlanPhase
from mde_contracts.ports import (
    ApprovalRepo,
    BriefRepo,
    EventRepo,
    JobRepo,
    ResearchRepo,
    SessionRepo,
    SpecRepo,
    Store,
    UnitOfWork,
)
from mde_contracts.records import (
    BriefVersionRecord,
    JobRecord,
    PlanJobPayload,
    SessionRecord,
    SpecVersionRecord,
)


@dataclass
class Ledger:
    sessions: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # tenant, id
    jobs: list[tuple[str, str, str]] = field(
        default_factory=list[tuple[str, str, str]]
    )  # tenant, session, job id


class _TrackingJobs:
    def __init__(self, inner: JobRepo, tenant_id: str | None, ledger: Ledger) -> None:
        self._inner = inner
        self._tenant_id = tenant_id
        self._ledger = ledger

    async def enqueue_plan(
        self, session_id: str, tenant_id: str, payload: PlanJobPayload
    ) -> JobRecord:
        job = await self._inner.enqueue_plan(session_id, tenant_id, payload)
        self._ledger.jobs.append((tenant_id, session_id, job.id))
        return job

    async def get(self, job_id: str) -> JobRecord | None:
        return await self._inner.get(job_id)

    async def active_for_session(self, session_id: str) -> JobRecord | None:
        return await self._inner.active_for_session(session_id)

    async def claim(self, worker_id: str) -> JobRecord | None:
        return await self._inner.claim(worker_id)

    async def heartbeat(self, job_id: str, worker_id: str) -> None:
        await self._inner.heartbeat(job_id, worker_id)

    async def set_phase(self, job_id: str, phase: PlanPhase) -> None:
        await self._inner.set_phase(job_id, phase)

    async def complete(self, job_id: str, *, error: str | None = None) -> None:
        await self._inner.complete(job_id, error=error)

    async def cancel(self, session_id: str) -> bool:
        return await self._inner.cancel(session_id)

    async def is_cancel_requested(self, job_id: str) -> bool:
        return await self._inner.is_cancel_requested(job_id)

    async def recover_stuck(self, *, older_than_seconds: float) -> int:
        return await self._inner.recover_stuck(older_than_seconds=older_than_seconds)


class _TrackingSessions:
    def __init__(self, inner: SessionRepo, ledger: Ledger) -> None:
        self._inner = inner
        self._ledger = ledger

    async def create(self, rec: SessionRecord) -> SessionRecord:
        stored = await self._inner.create(rec)
        self._ledger.sessions.append((stored.tenant_id, stored.id))
        return stored

    async def get(self, session_id: str, *, for_update: bool = False) -> SessionRecord | None:
        return await self._inner.get(session_id, for_update=for_update)

    async def save(self, rec: SessionRecord, *, expected_version: int) -> SessionRecord:
        return await self._inner.save(rec, expected_version=expected_version)

    async def list_for_owner(self, owner_id: str, *, limit: int = 50) -> list[SessionRecord]:
        return await self._inner.list_for_owner(owner_id, limit=limit)


class _TrackingUow:
    sessions: SessionRepo
    events: EventRepo
    briefs: BriefRepo
    research: ResearchRepo
    specs: SpecRepo
    approvals: ApprovalRepo
    jobs: JobRepo

    def __init__(self, inner: UnitOfWork, tenant_id: str | None, ledger: Ledger) -> None:
        self._inner = inner
        self.sessions = _TrackingSessions(inner.sessions, ledger)
        self.events = inner.events
        self.briefs = inner.briefs
        self.research = inner.research
        self.specs = inner.specs
        self.approvals = inner.approvals
        self.jobs = _TrackingJobs(inner.jobs, tenant_id, ledger)

    async def commit(self) -> None:
        await self._inner.commit()

    async def rollback(self) -> None:
        await self._inner.rollback()


class TrackingStore:
    """A ``Store`` that delegates everything and records created sessions and enqueued jobs."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.ledger = Ledger()

    def begin(self, tenant_id: str | None) -> AbstractAsyncContextManager[UnitOfWork]:
        return self._begin(tenant_id)

    @asynccontextmanager
    async def _begin(self, tenant_id: str | None) -> AsyncGenerator[UnitOfWork]:
        async with self.inner.begin(tenant_id) as uow:
            yield _TrackingUow(uow, tenant_id, self.ledger)


class Peek:
    """Read what the flow stored, through the ordinary repositories (works on any ``Store``)."""

    def __init__(self, store: TrackingStore) -> None:
        self._store = store

    @property
    def _ledger(self) -> Ledger:
        return self._store.ledger

    async def sessions(self) -> list[SessionRecord]:
        """Every session created through the store, oldest first."""
        out: list[SessionRecord] = []
        for tenant, sid in self._ledger.sessions:
            async with self._store.begin(tenant) as uow:
                rec = await uow.sessions.get(sid)
            if rec is not None:
                out.append(rec)
        return out

    async def session(self, session_id: str) -> SessionRecord:
        for rec in await self.sessions():
            if rec.id == session_id:
                return rec
        raise LookupError(session_id)

    def _tenant_of(self, session_id: str) -> str:
        for tenant, sid in self._ledger.sessions:
            if sid == session_id:
                return tenant
        raise LookupError(session_id)

    async def approvals(self, session_id: str) -> list[ApprovalRecord]:
        """Both gates' approvals in the order they were made."""
        async with self._store.begin(self._tenant_of(session_id)) as uow:
            found = [
                *await uow.approvals.list(session_id, "brief"),
                *await uow.approvals.list(session_id, "plan"),
            ]
        return sorted(found, key=lambda a: (a.at, a.id))

    async def specs(self, session_id: str) -> list[SpecVersionRecord]:
        async with self._store.begin(self._tenant_of(session_id)) as uow:
            return await uow.specs.list_versions(session_id)

    async def briefs(self, session_id: str) -> list[BriefVersionRecord]:
        async with self._store.begin(self._tenant_of(session_id)) as uow:
            latest = await uow.briefs.latest(session_id)
            versions = range(1, (latest.brief.version if latest else 0) + 1)
            found = [await uow.briefs.get(session_id, v) for v in versions]
        return [b for b in found if b is not None]

    async def jobs(self, session_id: str | None = None) -> list[JobRecord]:
        """Jobs enqueued through the store (oldest first), for one session or all of them."""
        out: list[JobRecord] = []
        for tenant, sid, job_id in self._ledger.jobs:
            if session_id is not None and sid != session_id:
                continue
            async with self._store.begin(tenant) as uow:
                job = await uow.jobs.get(job_id)
            if job is not None:
                out.append(job)
        return out

    async def outbound(self, session_id: str) -> list[OutboundEvent]:
        async with self._store.begin(self._tenant_of(session_id)) as uow:
            return await uow.events.list_outbound(session_id)
