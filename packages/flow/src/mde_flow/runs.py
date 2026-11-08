"""Run management: one active run per session, cooperative cancellation, the planning queue.

* **One active run per session.** ``start_run`` enqueues through ``JobRepo.enqueue_plan`` (which
  raises :class:`Conflict` when a run is already queued or running) behind an in-process advisory
  guard. ``JobRepo.cancel`` cancels a run no worker has started outright, so it never blocks the
  next one; a run that is still winding down raises ``Conflict`` and the caller shows a friendly
  "still stopping" notice.
* **Cooperative cancellation.** Every run has a :class:`CancellationToken`. Cancelling is two
  parts: ``cancel_run`` flips the database flag (inside the caller's unit of work, so it commits or
  rolls back with the state change), and ``cancel_token`` flips the in-process token **after** the
  commit (a rolled-back Stop must not cancel a run). Workers also poll the database flag at every
  checkpoint and progress event, which is how a cancel reaches a worker in another process.
* **Queue during PLANNING.** Messages typed while a run is active are stored on the session
  (``queued_messages``) and applied once the plan is ready (spec 3.6).
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass

from mde_contracts.ports import Store, UnitOfWork
from mde_contracts.records import JobRecord, PlanJobPayload, SessionRecord
from mde_core.cancel import CancellationToken
from mde_core.errors import Conflict, NotFound
from mde_core.redact import redact_text

MAX_QUEUED_MESSAGES = 25
MAX_QUEUED_CHARS = 4000


@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool  # there was an active run and it is now flagged
    job_id: str | None = None
    was_started: bool = False  # False: the job never ran, so it was retired immediately


class RunManager:
    def __init__(self) -> None:
        # weak: a lock lives while someone holds or waits on it, then is dropped (no leak per session)
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._tokens: dict[str, CancellationToken] = {}
        self._starting: set[str] = set()

    # ------------------------------------------------------------------ locks and tokens

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """In-process advisory lock; the database row lock covers other processes."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def token(self, job_id: str) -> CancellationToken:
        return self._tokens.setdefault(job_id, CancellationToken())

    def cancel_token(self, job_id: str, reason: str = "stopped") -> None:
        token = self._tokens.get(job_id)  # never create one just to cancel it
        if token is not None:
            token.cancel(reason)

    def forget(self, job_id: str) -> None:
        self._tokens.pop(job_id, None)

    # ------------------------------------------------------------------ start / cancel

    async def start_run(
        self, uow: UnitOfWork, *, session_id: str, tenant_id: str, payload: PlanJobPayload
    ) -> JobRecord:
        """Enqueue the session's one run, or raise :class:`Conflict`."""
        if session_id in self._starting:
            raise Conflict("a run is already being started for this session")
        self._starting.add(session_id)
        try:
            active = await uow.jobs.active_for_session(session_id)
            if active is not None:
                raise Conflict(
                    "the session already has an active run",
                    details={"job_id": active.id, "cancel_requested": active.cancel_requested},
                )
            return await uow.jobs.enqueue_plan(session_id, tenant_id, payload)
        finally:
            self._starting.discard(session_id)

    async def cancel_run(self, uow: UnitOfWork, session_id: str) -> CancelOutcome:
        """Flag the session's active run for cancellation (database side only). A run no worker
        has started is cancelled outright by the store, so it never blocks the next one; a running
        one winds down on its own (its worker sees the flag)."""
        job = await uow.jobs.active_for_session(session_id)
        if job is None:
            return CancelOutcome(False)
        await uow.jobs.cancel(session_id)
        started = job.state == "running"
        if not started:
            self.forget(job.id)
        return CancelOutcome(True, job.id, started)

    async def finish(self, uow: UnitOfWork, job: JobRecord, worker_id: str, *, error: str) -> bool:
        """End a run this worker owns, for good. ``JobRepo.complete(error=...)`` alone would requeue
        the job while attempts remain, but the flow has already put the session back (a stop, a
        failure with its notice): a retry would find nothing to do and block the next run. So the
        run is flagged as cancelled first, which makes ``complete`` end it ``cancelled`` with the
        reason kept in ``error``. Returns False when this worker no longer owns the job (it was
        recovered, finished or cancelled by someone else): nothing is touched then."""
        await uow.sessions.get(job.session_id, for_update=True)  # no run can be enqueued meanwhile
        current = await uow.jobs.get(job.id)
        if current is None or current.state != "running" or current.lock_owner != worker_id:
            return False
        if not current.cancel_requested:
            await uow.jobs.cancel(job.session_id)  # the session's one active run is this job
        await uow.jobs.complete(job.id, error=error)
        return True

    async def checkpoint(
        self, store: Store, job: JobRecord, token: CancellationToken, worker_id: str
    ) -> None:
        """Between steps and before every call: raise ``CallCancelled`` if the run was stopped, or
        if this worker lost the job (``heartbeat`` refuses a job that is no longer running under
        it)."""
        token.check()
        try:
            async with store.begin(job.tenant_id) as uow:
                if await uow.jobs.is_cancel_requested(job.id):
                    token.cancel("cancel requested")
                else:
                    await uow.jobs.heartbeat(job.id, worker_id)
        except (Conflict, NotFound):
            token.cancel("lost the job")
        token.check()

    # ------------------------------------------------------------------ queue during PLANNING

    @staticmethod
    def queue_message(rec: SessionRecord, text: str) -> tuple[SessionRecord, bool]:
        """Append a message typed during PLANNING (secrets redacted). Returns (record, accepted)."""
        clean, _ = redact_text(text.strip())
        if not clean:
            return rec, True
        if len(rec.queued_messages) >= MAX_QUEUED_MESSAGES:
            return rec, False
        queue = [*rec.queued_messages, clean[:MAX_QUEUED_CHARS]]
        return rec.model_copy(update={"queued_messages": queue}), True
