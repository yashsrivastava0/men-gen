"""Job workers: ``JobWorker`` (production loop) and ``InlineJobRunner`` (deterministic, tests).

A worker claims a job in system scope, then runs the handler while a watcher task (a) heartbeats
the job so ``recover_stuck`` leaves it alone and (b) polls ``is_cancel_requested`` and bridges it
to the handler's ``CancellationToken``. Everything after the claim runs bound to the job's own
tenant. Outcome mapping:

* handler returns, no cancellation requested        -> ``complete()``       (succeeded)
* handler raises ``CallCancelled`` or returns after
  a cancel request was seen                         -> ``complete(error=)`` (cancelled)
* handler raises anything else                      -> ``complete(error=)`` (requeued while
  attempts remain, ``failed`` after ``max_attempts``)
* the heartbeat is rejected (the job was recovered and may run elsewhere) -> the handler is
  cancelled and the worker does NOT complete the job.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from mde_contracts.ports import Store
from mde_contracts.records import JobRecord
from mde_core.cancel import CancellationToken
from mde_core.errors import CallCancelled, Conflict, NotFound

JobHandler = Callable[[JobRecord, CancellationToken], Awaitable[None]]

_log = structlog.get_logger("mde_persistence.worker")


@dataclass
class _Outcome:
    cancel_seen: bool = False
    lock_lost: bool = False


class _Runner:
    """Claim -> handle -> complete, shared by the worker and the inline runner."""

    def __init__(self, store: Store, handler: JobHandler, worker_id: str) -> None:
        self._store = store
        self._handler = handler
        self.worker_id = worker_id

    async def _claim(self) -> JobRecord | None:
        async with self._store.begin(None) as uow:
            return await uow.jobs.claim(self.worker_id)

    async def _complete(self, job: JobRecord, error: str | None) -> None:
        async with self._store.begin(job.tenant_id) as uow:
            await uow.jobs.complete(job.id, error=error)

    async def _cancel_requested(self, job: JobRecord) -> bool:
        try:
            async with self._store.begin(job.tenant_id) as uow:
                return await uow.jobs.is_cancel_requested(job.id)
        except NotFound:
            return False

    async def _watch(self, job: JobRecord, token: CancellationToken, outcome: _Outcome) -> None:
        """Overridden by the worker; the inline runner does not watch."""
        return None

    async def _process(self, job: JobRecord) -> None:
        token = CancellationToken()
        outcome = _Outcome()
        watcher = asyncio.create_task(self._watch(job, token, outcome))
        error: str | None = None
        try:
            await self._handler(job, token)
        except CallCancelled as exc:
            error = exc.message or "cancelled"
        except Exception as exc:
            _log.warning("job_handler_failed", job_id=job.id, error=type(exc).__name__)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if outcome.lock_lost:
            _log.warning("job_lock_lost", job_id=job.id, worker_id=self.worker_id)
            return
        if error is None and not outcome.cancel_seen:
            outcome.cancel_seen = await self._cancel_requested(job)  # late request, or inline
        if error is None and outcome.cancel_seen:
            error = "cancelled"
        try:
            await self._complete(job, error)
        except (Conflict, NotFound) as exc:
            _log.warning("job_complete_rejected", job_id=job.id, reason=exc.message)


class JobWorker(_Runner):
    """Polling worker. ``run_once`` handles at most one job; ``run_forever`` loops until stopped.

    ``heartbeat_interval`` must be well below the ``older_than_seconds`` used for recovery.
    ``cancel_poll_interval`` (default: the smaller of the heartbeat interval and one second) is how
    often the watcher looks at ``cancel_requested``. When ``recover_after`` is set, an idle
    ``run_forever`` also releases jobs whose heartbeat is older than that many seconds.
    """

    def __init__(
        self,
        store: Store,
        handler: JobHandler,
        worker_id: str,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 10.0,
        *,
        cancel_poll_interval: float | None = None,
        recover_after: float | None = None,
    ) -> None:
        super().__init__(store, handler, worker_id)
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._cancel_poll = (
            cancel_poll_interval
            if cancel_poll_interval is not None
            else min(heartbeat_interval, 1.0)
        )
        self._recover_after = recover_after

    async def run_once(self) -> bool:
        """Claim and process one job. Returns False when the queue was empty."""
        job = await self._claim()
        if job is None:
            return False
        await self._process(job)
        return True

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Process jobs until ``stop_event`` is set (the running job is finished first).

        A database error (restart, network blip) is logged and retried after ``poll_interval``;
        the loop only ends when asked to."""
        while not stop_event.is_set():
            try:
                if await self.run_once():
                    continue
                if self._recover_after is not None:
                    async with self._store.begin(None) as uow:
                        await uow.jobs.recover_stuck(older_than_seconds=self._recover_after)
            except Exception as exc:
                _log.warning(
                    "worker_loop_error", worker_id=self.worker_id, error=type(exc).__name__
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)

    async def _watch(self, job: JobRecord, token: CancellationToken, outcome: _Outcome) -> None:
        loop = asyncio.get_running_loop()
        last_beat = loop.time()
        while True:
            await asyncio.sleep(self._cancel_poll)
            beat_due = loop.time() - last_beat >= self._heartbeat_interval
            try:
                async with self._store.begin(job.tenant_id) as uow:
                    if beat_due:
                        await uow.jobs.heartbeat(job.id, self.worker_id)
                        last_beat = loop.time()
                    requested = await uow.jobs.is_cancel_requested(job.id)
            except (Conflict, NotFound):
                outcome.lock_lost = True
                token.cancel("lock_lost")
                return
            except Exception as exc:  # a transient database error must not kill the watcher
                _log.warning("job_watch_error", job_id=job.id, error=type(exc).__name__)
                continue
            if requested and not token.cancelled:
                outcome.cancel_seen = True
                token.cancel("cancel_requested")


class InlineJobRunner(_Runner):
    """Runs queued jobs inline and deterministically (no watcher task, no clock, no polling).

    ``run_next()`` claims the oldest queued job, awaits the handler and completes the job with the
    same outcome mapping as ``JobWorker``; it returns the job as stored afterwards, or ``None``
    when nothing was queued. A job cancelled while queued is never claimed. A handler that asks
    for cancellation itself (``uow.jobs.cancel(...)``) and returns is recorded as ``cancelled``.
    """

    def __init__(self, store: Store, handler: JobHandler, worker_id: str = "inline") -> None:
        super().__init__(store, handler, worker_id)

    async def run_next(self) -> JobRecord | None:
        job = await self._claim()
        if job is None:
            return None
        await self._process(job)
        async with self._store.begin(job.tenant_id) as uow:
            return await uow.jobs.get(job.id)

    async def run_all(self, *, limit: int = 100) -> list[JobRecord]:
        """Run queued jobs until the queue is empty (``limit`` guards against requeue loops)."""
        done: list[JobRecord] = []
        for _ in range(limit):
            finished = await self.run_next()
            if finished is None:
                break
            done.append(finished)
        return done
