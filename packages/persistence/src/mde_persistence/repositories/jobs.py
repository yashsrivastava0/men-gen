"""``JobRepo``: the Postgres-backed queue (brief 8.8).

* One active (queued or running) run per session: the partial unique index
  ``uq_job_one_active_per_session`` is the guarantee; ``enqueue_plan`` also takes a transaction
  advisory lock per session so a losing enqueue gets a clean ``Conflict`` instead of a failed
  statement (which would poison the surrounding transaction).
* ``claim`` uses ``FOR UPDATE SKIP LOCKED``: every queued job goes to exactly one worker.
* ``claim`` and ``recover_stuck`` are the only system-scope operations (``tenant_id=None``); every
  other method is bound to one tenant.
* Time is read from the injected clock, so recovery is testable with a fake clock.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mde_contracts.gates import PlanPhase
from mde_contracts.records import JobRecord, PlanJobPayload
from mde_core.clock import Clock
from mde_core.errors import Conflict, NotFound, TenantViolation
from mde_core.ids import new_id
from mde_persistence.jsonutil import clean_text, dump, utc, utc_required
from mde_persistence.models import JOB_ACTIVE_STATES, JobRow
from mde_persistence.repositories.base import RepoBase, tbl

JOB = tbl(JobRow)

ERROR_MAX_CHARS = 4000


def _job_record(row: Any) -> JobRecord:
    return JobRecord(
        id=row.id,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        kind=row.kind,
        state=row.state,
        phase=row.phase,
        payload=PlanJobPayload.model_validate(row.payload),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        lock_owner=row.lock_owner,
        heartbeat_at=utc(row.heartbeat_at),
        cancel_requested=row.cancel_requested,
        error=row.error,
        created_at=utc_required(row.created_at),
        updated_at=utc_required(row.updated_at),
    )


def _clip(error: str) -> str:
    return (clean_text(error) or "")[:ERROR_MAX_CHARS]


class PgJobRepo(RepoBase):
    def __init__(
        self,
        session: AsyncSession,
        tenant_id: str | None,
        clock: Clock,
        *,
        default_max_attempts: int = 3,
    ) -> None:
        super().__init__(session, tenant_id, clock)
        self._default_max_attempts = default_max_attempts

    # ------------------------------------------------------------------ tenant scope

    async def enqueue_plan(
        self, session_id: str, tenant_id: str, payload: PlanJobPayload
    ) -> JobRecord:
        """Queue a plan run. ``Conflict`` if the session already has a queued or running one."""
        tenant = self.own(tenant_id)
        await self.require_session(session_id)
        await self._s.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"mde:job:{session_id}", 0)))
        )
        busy = await self._s.execute(
            select(JOB.c.id).where(
                JOB.c.session_id == session_id, JOB.c.state.in_(JOB_ACTIVE_STATES)
            )
        )
        if busy.first() is not None:
            raise Conflict(f"session {session_id} already has an active run")
        now = self.now()
        stmt = (
            pg_insert(JOB)
            .values(
                id=new_id("job"),
                session_id=session_id,
                tenant_id=tenant,
                kind="plan",
                state="queued",
                phase=None,
                payload=dump(payload),
                attempts=0,
                max_attempts=self._default_max_attempts,
                cancel_requested=False,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()  # the unique partial index, if the advisory lock was bypassed
            .returning(*JOB.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"session {session_id} already has an active run")
        return _job_record(row)

    async def get(self, job_id: str) -> JobRecord | None:
        stmt = select(JOB).where(JOB.c.id == job_id, JOB.c.tenant_id == self.tenant)
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _job_record(row)

    async def active_for_session(self, session_id: str) -> JobRecord | None:
        stmt = select(JOB).where(
            JOB.c.session_id == session_id,
            JOB.c.tenant_id == self.tenant,
            JOB.c.state.in_(JOB_ACTIVE_STATES),
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _job_record(row)

    async def heartbeat(self, job_id: str, worker_id: str) -> None:
        """Refresh the heartbeat. ``Conflict`` when the job is no longer running under this
        worker (it was recovered, cancelled or finished): the worker has lost its lock."""
        stmt = (
            update(JOB)
            .where(
                JOB.c.id == job_id,
                JOB.c.tenant_id == self.tenant,
                JOB.c.state == "running",
                JOB.c.lock_owner == worker_id,
            )
            .values(heartbeat_at=self.now(), updated_at=self.now())
            .returning(JOB.c.id)
        )
        if (await self._s.execute(stmt)).first() is None:
            await self._require_job(job_id)
            raise Conflict(f"job {job_id} is not running under worker {worker_id}")

    async def set_phase(self, job_id: str, phase: PlanPhase) -> None:
        stmt = (
            update(JOB)
            .where(
                JOB.c.id == job_id,
                JOB.c.tenant_id == self.tenant,
                JOB.c.state.in_(JOB_ACTIVE_STATES),
            )
            .values(phase=phase, updated_at=self.now())
            .returning(JOB.c.id)
        )
        if (await self._s.execute(stmt)).first() is None:
            await self._require_job(job_id)
            raise Conflict(f"job {job_id} is not active")

    async def complete(self, job_id: str, *, error: str | None = None) -> None:
        """Finish a running job.

        * ``error=None``: ``succeeded``.
        * ``error`` set and cancellation was requested: ``cancelled`` (no retry).
        * ``error`` set, attempts left (``attempts < max_attempts``): back to ``queued`` for
          another attempt (the error is kept as the last error).
        * ``error`` set, no attempts left: ``failed``.
        ``Conflict`` if the job is not running.
        """
        row = (
            await self._s.execute(
                select(JOB)
                .where(JOB.c.id == job_id, JOB.c.tenant_id == self.tenant)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise NotFound(f"job {job_id} not found")
        if row.state != "running":
            raise Conflict(f"job {job_id} is {row.state}, not running")
        values: dict[str, object] = {"updated_at": self.now()}
        if error is None:
            values.update(state="succeeded", lock_owner=None, error=None)
        elif row.cancel_requested:
            values.update(state="cancelled", lock_owner=None, error=_clip(error))
        elif row.attempts < row.max_attempts:
            values.update(state="queued", lock_owner=None, error=_clip(error))
        else:
            values.update(state="failed", lock_owner=None, error=_clip(error))
        await self._s.execute(update(JOB).where(JOB.c.id == job_id).values(**values))

    async def cancel(self, session_id: str) -> bool:
        """Cooperative cancel of the session's active run. A queued job is cancelled outright;
        a running one gets ``cancel_requested`` and its worker stops at the next check."""
        was_queued = JOB.c.state == "queued"
        stmt = (
            update(JOB)
            .where(
                JOB.c.session_id == session_id,
                JOB.c.tenant_id == self.tenant,
                JOB.c.state.in_(JOB_ACTIVE_STATES),
            )
            .values(
                cancel_requested=True,
                state=case((was_queued, "cancelled"), else_=JOB.c.state),
                error=case((was_queued, "cancelled before it started"), else_=JOB.c.error),
                updated_at=self.now(),
            )
            .returning(JOB.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def is_cancel_requested(self, job_id: str) -> bool:
        stmt = select(JOB.c.cancel_requested).where(
            JOB.c.id == job_id, JOB.c.tenant_id == self.tenant
        )
        value = (await self._s.execute(stmt)).scalar_one_or_none()
        if value is None:
            raise NotFound(f"job {job_id} not found")
        return bool(value)

    # ------------------------------------------------------------------ system scope

    async def claim(self, worker_id: str) -> JobRecord | None:
        """Take the oldest queued job (``FOR UPDATE SKIP LOCKED``): running, owned by
        ``worker_id``, heartbeat set, ``attempts`` + 1. System scope only."""
        self._require_system_scope()
        now = self.now()
        picked = (
            select(JOB.c.id)
            .where(JOB.c.state == "queued")
            .order_by(JOB.c.created_at, JOB.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .cte("picked")
        )
        stmt = (
            update(JOB)
            .where(JOB.c.id == picked.c.id)
            .values(
                state="running",
                lock_owner=worker_id,
                heartbeat_at=now,
                attempts=JOB.c.attempts + 1,
                updated_at=now,
            )
            .returning(*JOB.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _job_record(row)

    async def recover_stuck(self, *, older_than_seconds: float) -> int:
        """Release running jobs whose heartbeat is older than ``older_than_seconds``: back to
        ``queued`` while attempts remain, ``failed`` when they do not, ``cancelled`` when
        cancellation had been requested. Returns the number of jobs released. System scope only."""
        self._require_system_scope()
        cutoff = self.now() - timedelta(seconds=older_than_seconds)
        stale = (
            select(JOB.c.id)
            .where(JOB.c.state == "running", JOB.c.heartbeat_at < cutoff)
            .with_for_update(skip_locked=True)
            .cte("stale")
        )
        out_of_attempts = JOB.c.attempts >= JOB.c.max_attempts
        stmt = (
            update(JOB)
            .where(JOB.c.id == stale.c.id)
            .values(
                state=case(
                    (JOB.c.cancel_requested, "cancelled"),
                    (out_of_attempts, "failed"),
                    else_="queued",
                ),
                error=case(
                    (JOB.c.cancel_requested, "cancelled; its worker stopped heartbeating"),
                    (out_of_attempts, "worker stopped heartbeating; no attempts left"),
                    else_="worker stopped heartbeating; requeued",
                ),
                lock_owner=None,
                updated_at=self.now(),
            )
            .returning(JOB.c.id)
        )
        return len((await self._s.execute(stmt)).all())

    # ------------------------------------------------------------------ helpers

    def _require_system_scope(self) -> None:
        if self._tenant_id is not None:
            raise TenantViolation(
                "claim and recover_stuck are system-scope operations: use Store.begin(None)",
                details={"scope": self._tenant_id},
            )

    async def _require_job(self, job_id: str) -> None:
        stmt = select(JOB.c.id).where(JOB.c.id == job_id, JOB.c.tenant_id == self.tenant)
        if (await self._s.execute(stmt)).first() is None:
            raise NotFound(f"job {job_id} not found")
