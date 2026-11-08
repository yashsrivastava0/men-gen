"""``InMemoryStore``: a faithful in-memory implementation of ``Store`` / ``UnitOfWork`` and every
repository protocol in ``mde_contracts.ports`` (brief 8.1; D-010: in-memory fakes are not SQLite).

The fake is validated, not trusted: ``mde_persistence.testing_contract.CHECKS`` (the suite
``PostgresStore`` passes) runs against it in ``tests/integration/test_inmemory_store_contract.py``,
so a flow unit or property test built on this class says something about the real store. The rules
it keeps, each of them pinned by that suite:

* **Units of work** are transactional: mutations are applied with an undo log and rolled back on an
  exception (or ``rollback()``); ``commit()`` makes them permanent and, like Postgres, ends the
  transaction, so row locks are released and later work is a new transaction.
* **Row locks.** ``SessionRepo.get(for_update=True)``, ``SessionRepo.save`` and
  ``EventRepo.append_outbound`` take the session's row lock until the unit of work ends (Postgres
  ``SELECT ... FOR UPDATE`` and the ``UPDATE`` that bumps ``outbound_seq``); ``enqueue_plan`` takes
  the per-session advisory lock. A second unit of work waits.
* **Tenant isolation.** A unit of work bound to tenant A never sees tenant B's rows (reads return
  ``None`` or an empty list, writes ``NotFound``; a record of another tenant is a
  ``TenantViolation``). ``begin(None)`` is **system scope**: only ``JobRepo.claim`` and
  ``JobRepo.recover_stuck`` work there, everything else raises ``TenantViolation``.
* **Optimistic CAS** on ``SessionRepo.save`` (``Conflict`` on a stale ``expected_version``).
* **Idempotent inbound events**; outbound events get a per-session ``seq`` (strictly increasing)
  and the store's time; a repeated outbound event id is a ``Conflict``; ``replies_to`` filters by
  ``in_reply_to``.
* **Insert-only versions.** Adding a Brief, Research Note or spec version (or an approval id) that
  exists is a ``Conflict``. Only ``status``, ``git_sha`` and ``approved_at`` of a spec version can
  change, and the status only moves forward (draft -> approved -> handed_off; draft or approved ->
  superseded; a handed-off version stays handed off).
* **Job queue.** One active run per session; ``cancel`` cancels a still-queued job outright and
  flags a running one; ``complete`` needs a running job (``Conflict`` otherwise) and, with an error,
  requeues while ``attempts < max_attempts``, then fails, or ends ``cancelled`` after a cancel
  request; ``heartbeat`` raises ``Conflict`` when the lock was lost; ``recover_stuck`` returns the
  number of released jobs.
* Records are stored as JSON and re-validated on read, so aliasing and non-serialisable fields
  cannot hide in unit tests.

Known difference: writes of an open unit of work are visible to other units immediately (Postgres
is READ COMMITTED). The flow only writes under the session row lock, so it cannot observe this.
``open_units`` / ``peak_open_units`` count open units of work (pool connections, in Postgres terms),
which lets a test prove nothing is held across an engine call.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from mde_contracts.common import GateKind
from mde_contracts.events import InboundEvent, OutboundEvent
from mde_contracts.gates import ApprovalRecord, PlanPhase
from mde_contracts.ports import (
    ApprovalRepo,
    BriefRepo,
    EventRepo,
    JobRepo,
    ResearchRepo,
    SessionRepo,
    SpecRepo,
    UnitOfWork,
)
from mde_contracts.records import (
    BriefVersionRecord,
    JobRecord,
    PlanJobPayload,
    ResearchNoteRecord,
    SessionRecord,
    SpecStatus,
    SpecVersionRecord,
)
from mde_core.clock import Clock, FakeClock
from mde_core.errors import Conflict, NotFound, TenantViolation, ValidationFailed
from mde_core.ids import SequentialIds

M = TypeVar("M", bound=BaseModel)
K = TypeVar("K")
V = TypeVar("V")

_OUTBOUND = TypeAdapter(OutboundEvent)
_INBOUND = TypeAdapter(InboundEvent)

_SPEC_FORWARD: dict[str, set[str]] = {
    "draft": {"approved", "superseded"},
    "approved": {"handed_off", "superseded"},
    "handed_off": set(),  # a handed-off version is history: it is never superseded or reopened
    "superseded": set(),
}
_SPEC_STATUSES = frozenset(_SPEC_FORWARD)
_BRIEF_FORWARD: dict[str, set[str]] = {
    "draft": {"approved", "superseded"},
    "approved": {"superseded"},
    "superseded": set(),
}
_ACTIVE_JOB_STATES = {"queued", "running"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class _Data:
    """The shared 'database'. Values are JSON strings; keys carry the session id."""

    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.inbound: dict[str, dict[str, str]] = {}  # session -> event id -> json
        self.outbound: dict[str, dict[int, str]] = {}  # session -> seq -> json
        self.outbound_ids: dict[tuple[str, str], int] = {}  # (session, event id) -> seq
        self.outbound_seq: dict[str, int] = {}  # session -> last seq handed out
        self.briefs: dict[tuple[str, int], str] = {}
        self.research: dict[tuple[str, int], str] = {}
        self.specs: dict[tuple[str, int], str] = {}
        self.approvals: dict[str, str] = {}  # approval id -> json (ids are global, like the PK)
        self.jobs: dict[str, str] = {}
        self.job_order: list[str] = []


class InMemoryStore:
    """Implements ``mde_contracts.ports.Store``."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        ids: Callable[[str], str] | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.clock: Clock = clock or FakeClock()
        self.ids: Callable[[str], str] = ids or SequentialIds()
        self.max_attempts = max_attempts
        self.data = _Data()
        self._locks: dict[str, asyncio.Lock] = {}
        self.begin_count = 0
        self.open_units = 0
        self.peak_open_units = 0

    @asynccontextmanager
    async def begin(self, tenant_id: str | None) -> AsyncGenerator[UnitOfWork]:
        uow = _Uow(self, tenant_id)
        self.begin_count += 1
        self.open_units += 1
        self.peak_open_units = max(self.peak_open_units, self.open_units)
        try:
            yield uow
        except BaseException:
            uow.abort()
            raise
        else:
            uow.finish()
        finally:
            self.open_units -= 1

    # -- test conveniences (read-only views for assertions; they bypass tenant scoping) -----------
    def all_sessions(self) -> list[SessionRecord]:
        return [SessionRecord.model_validate_json(s) for s in self.data.sessions.values()]

    def outbound_events(self, session_id: str) -> list[OutboundEvent]:
        log = self.data.outbound.get(session_id, {})
        return [_OUTBOUND.validate_json(log[seq]) for seq in sorted(log)]

    def inbound_ids(self, session_id: str) -> list[str]:
        return list(self.data.inbound.get(session_id, {}))

    def all_approvals(self, session_id: str) -> list[ApprovalRecord]:
        found = [ApprovalRecord.model_validate_json(s) for s in self.data.approvals.values()]
        return sorted(
            (a for a in found if a.session_id == session_id), key=lambda a: (a.at or _EPOCH, a.id)
        )

    def all_specs(self, session_id: str) -> list[SpecVersionRecord]:
        keys = sorted(k for k in self.data.specs if k[0] == session_id)
        return [SpecVersionRecord.model_validate_json(self.data.specs[k]) for k in keys]

    def all_briefs(self, session_id: str) -> list[BriefVersionRecord]:
        keys = sorted(k for k in self.data.briefs if k[0] == session_id)
        return [BriefVersionRecord.model_validate_json(self.data.briefs[k]) for k in keys]

    def outbound_count(self, session_id: str) -> int:
        return len(self.data.outbound.get(session_id, {}))

    def all_jobs(self, session_id: str | None = None) -> list[JobRecord]:
        jobs = [JobRecord.model_validate_json(self.data.jobs[j]) for j in self.data.job_order]
        return [j for j in jobs if session_id is None or j.session_id == session_id]

    def lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


class _Uow:
    """One transaction bound to a tenant (or system scope)."""

    def __init__(self, store: InMemoryStore, tenant_id: str | None) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self._undo: list[Callable[[], None]] = []
        self._held: dict[str, asyncio.Lock] = {}
        self.sessions: SessionRepo = _Sessions(self)
        self.events: EventRepo = _Events(self)
        self.briefs: BriefRepo = _Briefs(self)
        self.research: ResearchRepo = _Research(self)
        self.specs: SpecRepo = _Specs(self)
        self.approvals: ApprovalRepo = _Approvals(self)
        self.jobs: JobRepo = _Jobs(self)

    # -- transaction plumbing -----------------------------------------------------------------
    async def commit(self) -> None:
        """Make the work permanent. Like Postgres this ends the transaction: row locks are
        released and anything after it runs in a new one."""
        self._undo.clear()
        self._release()

    async def rollback(self) -> None:
        self._rollback()
        self._release()

    def _rollback(self) -> None:
        while self._undo:
            self._undo.pop()()

    def _release(self) -> None:
        for lock in self._held.values():
            lock.release()
        self._held.clear()

    def abort(self) -> None:
        """Roll back everything and release the row locks (an exception left the unit of work)."""
        self._rollback()
        self._release()

    def finish(self) -> None:
        """Clean exit: make the changes permanent and release the row locks."""
        self._undo.clear()
        self._release()

    async def lock(self, session_id: str, *, advisory: bool = False) -> None:
        """Hold the session's row lock (or its job advisory lock) until the unit of work ends."""
        key = f"{'job' if advisory else 'row'}:{session_id}"
        if key in self._held:
            return
        lock = self.store.lock_for(key)
        await lock.acquire()
        self._held[key] = lock

    # -- scoped helpers -------------------------------------------------------------------------
    @property
    def data(self) -> _Data:
        return self.store.data

    @property
    def tenant(self) -> str:
        """The bound tenant. System scope has none and must not touch tenant data."""
        if self.tenant_id is None:
            raise TenantViolation(
                "system scope (tenant_id=None) may only claim and recover jobs",
                details={"scope": "system"},
            )
        return self.tenant_id

    def require_tenant_scope(self) -> None:
        """Raise ``TenantViolation`` in system scope."""
        _ = self.tenant

    def own(self, tenant_id: str) -> str:
        """Check that a record's tenant is the unit of work's tenant; return it."""
        bound = self.tenant
        if tenant_id != bound:
            raise TenantViolation(
                "record belongs to another tenant than the unit of work is bound to",
                details={"record_tenant": tenant_id, "bound_tenant": bound},
            )
        return bound

    def session(self, session_id: str) -> SessionRecord | None:
        """The session if it exists in this tenant (a foreign id looks like a missing one)."""
        tenant = self.tenant
        raw = self.data.sessions.get(session_id)
        if raw is None:
            return None
        rec = SessionRecord.model_validate_json(raw)
        return rec if rec.tenant_id == tenant else None

    def require_session(self, session_id: str) -> SessionRecord:
        rec = self.session(session_id)
        if rec is None:
            raise NotFound(f"session {session_id!r} not found")
        return rec

    def put(self, mapping: dict[K, V], key: K, value: V) -> None:
        if key in mapping:
            old = mapping[key]

            def restore() -> None:
                mapping[key] = old

            self._undo.append(restore)
        else:

            def remove() -> None:
                mapping.pop(key, None)

            self._undo.append(remove)
        mapping[key] = value

    def append(self, seq: list[V], value: V) -> None:
        seq.append(value)

        def remove() -> None:
            if value in seq:
                seq.remove(value)

        self._undo.append(remove)

    def now(self) -> datetime:
        return self.store.clock.now()


# --------------------------------------------------------------------------- repositories


class _Sessions:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def create(self, rec: SessionRecord) -> SessionRecord:
        self._u.own(rec.tenant_id)
        if rec.id in self._u.data.sessions:
            raise Conflict(f"session {rec.id!r} already exists")
        now = self._u.now()
        stored = rec.model_copy(update={"created_at": rec.created_at or now, "updated_at": now})
        raw = stored.model_dump_json()
        self._u.put(self._u.data.sessions, rec.id, raw)
        return SessionRecord.model_validate_json(raw)

    async def get(self, session_id: str, *, for_update: bool = False) -> SessionRecord | None:
        if self._u.session(session_id) is None:
            return None  # nothing to lock: a foreign or unknown id
        if for_update:
            await self._u.lock(session_id)
        return self._u.session(session_id)  # re-read: it may have changed while we waited

    async def save(self, rec: SessionRecord, *, expected_version: int) -> SessionRecord:
        self._u.own(rec.tenant_id)
        self._u.require_session(rec.id)
        await self._u.lock(rec.id)  # the UPDATE takes the row lock; a loser then sees a new version
        current = self._u.require_session(rec.id)
        if current.version != expected_version:
            raise Conflict(
                f"session {rec.id}: stale write (expected version {expected_version})",
                details={"session_id": rec.id, "expected_version": expected_version},
            )
        stored = rec.model_copy(
            update={
                "version": expected_version + 1,
                "updated_at": self._u.now(),
                "tenant_id": current.tenant_id,
                "created_at": current.created_at,
            }
        )
        raw = stored.model_dump_json()
        self._u.put(self._u.data.sessions, rec.id, raw)
        return SessionRecord.model_validate_json(raw)

    async def list_for_owner(self, owner_id: str, *, limit: int = 50) -> list[SessionRecord]:
        tenant = self._u.tenant
        found = [SessionRecord.model_validate_json(s) for s in self._u.data.sessions.values()]
        mine = [r for r in found if r.tenant_id == tenant and r.owner_id == owner_id]
        mine.sort(key=lambda r: (r.updated_at or _EPOCH, r.id), reverse=True)
        return mine[:limit]


class _Events:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def record_inbound(self, session_id: str, event: InboundEvent) -> bool:
        self._u.require_session(session_id)
        seen = self._u.data.inbound.setdefault(session_id, {})
        if event.event_id in seen:
            return False
        self._u.put(seen, event.event_id, _INBOUND.dump_json(event).decode())
        return True

    async def append_outbound(self, session_id: str, event: OutboundEvent) -> OutboundEvent:
        self._u.require_tenant_scope()
        if event.session_id != session_id:
            raise ValidationFailed(
                f"event {event.event_id} belongs to session {event.session_id}, not {session_id}"
            )
        self._u.require_session(session_id)
        await self._u.lock(session_id)  # bumping outbound_seq takes the row lock until commit
        data = self._u.data
        seq = data.outbound_seq.get(session_id, 0) + 1
        self._u.put(data.outbound_seq, session_id, seq)
        if (session_id, event.event_id) in data.outbound_ids:
            raise Conflict(f"event {event.event_id} was already recorded for {session_id}")
        stored = event.model_copy(update={"seq": seq, "at": event.at or self._u.now()})
        self._u.put(
            data.outbound.setdefault(session_id, {}), seq, _OUTBOUND.dump_json(stored).decode()
        )
        self._u.put(data.outbound_ids, (session_id, event.event_id), seq)
        return stored

    def _log(self, session_id: str) -> dict[int, str]:
        if self._u.session(session_id) is None:
            return {}
        return self._u.data.outbound.get(session_id, {})

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[OutboundEvent]:
        log = self._log(session_id)
        wanted = sorted(seq for seq in log if seq > after_seq)[:limit]  # parse only what is read
        return [_OUTBOUND.validate_json(log[seq]) for seq in wanted]

    async def replies_to(self, session_id: str, inbound_event_id: str) -> list[OutboundEvent]:
        log = self._log(session_id)
        events = (_OUTBOUND.validate_json(log[seq]) for seq in sorted(log))
        return [e for e in events if e.in_reply_to == inbound_event_id]


class _Briefs:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def add(self, rec: BriefVersionRecord) -> BriefVersionRecord:
        self._u.own(rec.tenant_id)
        self._u.require_session(rec.session_id)
        key = (rec.session_id, rec.brief.version)
        if key in self._u.data.briefs:
            raise Conflict(f"brief {rec.session_id} v{rec.brief.version} already exists")
        stored = rec.model_copy(update={"created_at": rec.created_at or self._u.now()})
        raw = stored.model_dump_json()
        self._u.put(self._u.data.briefs, key, raw)
        return BriefVersionRecord.model_validate_json(raw)

    async def get(self, session_id: str, version: int) -> BriefVersionRecord | None:
        if self._u.session(session_id) is None:
            return None
        raw = self._u.data.briefs.get((session_id, version))
        return None if raw is None else BriefVersionRecord.model_validate_json(raw)

    async def latest(self, session_id: str) -> BriefVersionRecord | None:
        if self._u.session(session_id) is None:
            return None
        versions = [v for (sid, v) in self._u.data.briefs if sid == session_id]
        return await self.get(session_id, max(versions)) if versions else None

    async def set_status(self, session_id: str, version: int, status: str) -> None:
        if status not in _BRIEF_FORWARD:
            raise ValidationFailed(f"unknown brief status {status!r}")
        rec = await self.get(session_id, version)
        if rec is None:
            raise NotFound(f"brief {session_id} v{version} not found")
        current = rec.brief.status
        if status == current:
            return
        if status not in _BRIEF_FORWARD[current]:
            raise Conflict(
                f"brief {session_id} v{version}: illegal status move {current} -> {status}"
            )
        updated = rec.model_copy(update={"brief": rec.brief.model_copy(update={"status": status})})
        self._u.put(self._u.data.briefs, (session_id, version), updated.model_dump_json())


class _Research:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def add(self, rec: ResearchNoteRecord) -> ResearchNoteRecord:
        self._u.own(rec.tenant_id)
        self._u.require_session(rec.session_id)
        key = (rec.session_id, rec.note.version)
        if key in self._u.data.research:
            raise Conflict(f"research note {rec.session_id} v{rec.note.version} already exists")
        stored = rec.model_copy(update={"created_at": rec.created_at or self._u.now()})
        raw = stored.model_dump_json()
        self._u.put(self._u.data.research, key, raw)
        return ResearchNoteRecord.model_validate_json(raw)

    async def latest(self, session_id: str) -> ResearchNoteRecord | None:
        if self._u.session(session_id) is None:
            return None
        versions = [v for (sid, v) in self._u.data.research if sid == session_id]
        if not versions:
            return None
        return ResearchNoteRecord.model_validate_json(
            self._u.data.research[(session_id, max(versions))]
        )


class _Specs:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def add(self, rec: SpecVersionRecord) -> SpecVersionRecord:
        self._u.own(rec.tenant_id)
        self._u.require_session(rec.session_id)
        key = (rec.session_id, rec.version)
        if key in self._u.data.specs:
            raise Conflict(f"spec {rec.session_id} v{rec.version} already exists")
        stored = rec.model_copy(update={"created_at": rec.created_at or self._u.now()})
        raw = stored.model_dump_json()
        self._u.put(self._u.data.specs, key, raw)
        return SpecVersionRecord.model_validate_json(raw)

    async def get(self, session_id: str, version: int) -> SpecVersionRecord | None:
        if self._u.session(session_id) is None:
            return None
        raw = self._u.data.specs.get((session_id, version))
        return None if raw is None else SpecVersionRecord.model_validate_json(raw)

    async def latest(self, session_id: str) -> SpecVersionRecord | None:
        versions = await self.list_versions(session_id)
        return versions[-1] if versions else None

    async def list_versions(self, session_id: str) -> list[SpecVersionRecord]:
        if self._u.session(session_id) is None:
            return []
        keys = sorted(k for k in self._u.data.specs if k[0] == session_id)
        return [SpecVersionRecord.model_validate_json(self._u.data.specs[k]) for k in keys]

    async def set_status(
        self,
        session_id: str,
        version: int,
        status: SpecStatus,
        *,
        git_sha: str | None = None,
        approved_at: datetime | None = None,
    ) -> SpecVersionRecord:
        if status not in _SPEC_STATUSES:
            raise ValidationFailed(f"unknown spec status {status!r}")
        rec = await self.get(session_id, version)
        if rec is None:
            raise NotFound(f"spec {session_id} v{version} not found")
        label = f"spec {session_id} v{version}"
        if status != rec.status and status not in _SPEC_FORWARD[rec.status]:
            raise Conflict(f"{label}: illegal status move {rec.status} -> {status}")
        changes: dict[str, object] = {}
        if status != rec.status:
            changes["status"] = status
        if git_sha is not None:
            if rec.git_sha is not None and rec.git_sha != git_sha:
                raise Conflict(f"{label}: git_sha is already set")
            if rec.git_sha is None:
                changes["git_sha"] = git_sha
        stamp = approved_at
        if stamp is None and status == "approved" and rec.status != "approved":
            stamp = self._u.now()  # moving to approved stamps the time when none is given
        if stamp is not None:
            if rec.approved_at is not None and rec.approved_at != stamp:
                raise Conflict(f"{label}: approved_at is already set")
            if rec.approved_at is None:
                changes["approved_at"] = stamp
        if not changes:
            return rec
        updated = rec.model_copy(update={**changes})
        self._u.put(self._u.data.specs, (session_id, version), updated.model_dump_json())
        return updated


class _Approvals:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def add(self, rec: ApprovalRecord) -> ApprovalRecord:
        self._u.require_session(rec.session_id)
        if rec.id in self._u.data.approvals:
            raise Conflict(f"approval {rec.id} already exists")
        stored = rec.model_copy(update={"at": rec.at or self._u.now()})
        raw = stored.model_dump_json()
        self._u.put(self._u.data.approvals, rec.id, raw)
        return ApprovalRecord.model_validate_json(raw)

    async def list(
        self,
        session_id: str,
        gate: GateKind,
        *,
        version: int | None = None,
        hash: str | None = None,
    ) -> list[ApprovalRecord]:
        if self._u.session(session_id) is None:
            return []
        found = [ApprovalRecord.model_validate_json(s) for s in self._u.data.approvals.values()]
        out = [
            a
            for a in found
            if a.session_id == session_id
            and a.gate == gate
            and (version is None or a.version == version)
            and (hash is None or a.hash == hash)
        ]
        return sorted(out, key=lambda a: (a.at or _EPOCH, a.id))


class _Jobs:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    # -- helpers --------------------------------------------------------------------------------
    def _load(self, job_id: str) -> JobRecord | None:
        """The job if it exists in this tenant (system scope never reads by id)."""
        tenant = self._u.tenant
        raw = self._u.data.jobs.get(job_id)
        if raw is None:
            return None
        job = JobRecord.model_validate_json(raw)
        return job if job.tenant_id == tenant else None

    def _must(self, job_id: str) -> JobRecord:
        job = self._load(job_id)
        if job is None:
            raise NotFound(f"job {job_id} not found")
        return job

    def _store(self, job: JobRecord, **changes: object) -> JobRecord:
        stored = job.model_copy(update={**changes, "updated_at": self._u.now()})
        self._u.put(self._u.data.jobs, job.id, stored.model_dump_json())
        return stored

    def _all(self) -> list[JobRecord]:
        return [JobRecord.model_validate_json(self._u.data.jobs[j]) for j in self._u.data.job_order]

    def _active(self, session_id: str) -> JobRecord | None:
        tenant = self._u.tenant
        for job in self._all():
            if (
                job.session_id == session_id
                and job.tenant_id == tenant
                and job.state in _ACTIVE_JOB_STATES
            ):
                return job
        return None

    # -- tenant scope ---------------------------------------------------------------------------
    async def enqueue_plan(
        self, session_id: str, tenant_id: str, payload: PlanJobPayload
    ) -> JobRecord:
        self._u.own(tenant_id)
        self._u.require_session(session_id)
        await self._u.lock(session_id, advisory=True)  # pg_advisory_xact_lock per session
        if self._active(session_id) is not None:
            raise Conflict(f"session {session_id} already has an active run")
        now = self._u.now()
        job = JobRecord(
            id=self._u.store.ids("job"),
            session_id=session_id,
            tenant_id=tenant_id,
            payload=payload,
            max_attempts=self._u.store.max_attempts,
            created_at=now,
            updated_at=now,
        )
        self._u.put(self._u.data.jobs, job.id, job.model_dump_json())
        self._u.append(self._u.data.job_order, job.id)
        return job

    async def get(self, job_id: str) -> JobRecord | None:
        return self._load(job_id)

    async def active_for_session(self, session_id: str) -> JobRecord | None:
        return self._active(session_id)

    async def heartbeat(self, job_id: str, worker_id: str) -> None:
        job = self._must(job_id)
        if job.state != "running" or job.lock_owner != worker_id:
            raise Conflict(f"job {job_id} is not running under worker {worker_id}")
        self._store(job, heartbeat_at=self._u.now())

    async def set_phase(self, job_id: str, phase: PlanPhase) -> None:
        job = self._must(job_id)
        if job.state not in _ACTIVE_JOB_STATES:
            raise Conflict(f"job {job_id} is not active")
        self._store(job, phase=phase)

    async def complete(self, job_id: str, *, error: str | None = None) -> None:
        job = self._must(job_id)
        if job.state != "running":
            raise Conflict(f"job {job_id} is {job.state}, not running")
        if error is None:
            self._store(job, state="succeeded", lock_owner=None, error=None)
        elif job.cancel_requested:
            self._store(job, state="cancelled", lock_owner=None, error=error)
        elif job.attempts < job.max_attempts:
            self._store(job, state="queued", lock_owner=None, error=error)
        else:
            self._store(job, state="failed", lock_owner=None, error=error)

    async def cancel(self, session_id: str) -> bool:
        job = self._active(session_id)
        if job is None:
            return False
        if job.state == "queued":
            self._store(
                job, cancel_requested=True, state="cancelled", error="cancelled before it started"
            )
        else:
            self._store(job, cancel_requested=True)
        return True

    async def is_cancel_requested(self, job_id: str) -> bool:
        return self._must(job_id).cancel_requested

    # -- system scope ---------------------------------------------------------------------------
    def _require_system_scope(self) -> None:
        if self._u.tenant_id is not None:
            raise TenantViolation(
                "claim and recover_stuck are system-scope operations: use Store.begin(None)",
                details={"scope": self._u.tenant_id},
            )

    async def claim(self, worker_id: str) -> JobRecord | None:
        self._require_system_scope()
        for job in self._all():
            if job.state == "queued":
                now = self._u.now()
                return self._store(
                    job,
                    state="running",
                    lock_owner=worker_id,
                    heartbeat_at=now,
                    attempts=job.attempts + 1,
                )
        return None

    async def recover_stuck(self, *, older_than_seconds: float) -> int:
        self._require_system_scope()
        cutoff = self._u.now() - timedelta(seconds=older_than_seconds)
        released = 0
        for job in self._all():
            if job.state != "running" or job.heartbeat_at is None or not job.heartbeat_at < cutoff:
                continue
            if job.cancel_requested:
                state, why = "cancelled", "cancelled; its worker stopped heartbeating"
            elif job.attempts >= job.max_attempts:
                state, why = "failed", "worker stopped heartbeating; no attempts left"
            else:
                state, why = "queued", "worker stopped heartbeating; requeued"
            self._store(job, state=state, error=why, lock_owner=None)
            released += 1
        return released
