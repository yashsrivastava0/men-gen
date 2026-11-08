"""The job worker: runs plan jobs (research, build, Gate 2) outside the request that enqueued them.

* ``make_job_handler(flow, worker_id)`` adapts ``FlowService.run_plan_job`` to the persistence
  worker's ``JobHandler`` shape. The flow cancels a run in two ways (the in-process token of its
  ``RunManager`` and the database flag it polls at every checkpoint); the persistence worker adds a
  third (its watcher, which sees a flag set by another process, or a lost lock). The handler joins
  them: whichever token is cancelled stops the run.
* ``BackgroundWorker`` runs ``JobWorker.run_forever`` as an asyncio task. The API starts one in its
  lifespan (``WORKER_ENABLED=1``, the default); with ``WORKER_ENABLED=0`` run a separate process:
  ``python -m mde_context_api.worker`` (``--fake-builder`` for the scripted builder).

No unit of work or connection is held while the worker sleeps between polls: the persistence
worker claims, releases, then runs.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys

from mde_context_api import loop as _loop
from mde_context_api.settings import ApiSettings, load_api_settings
from mde_contracts.ports import Store
from mde_contracts.records import JobRecord
from mde_core.cancel import CancellationToken
from mde_core.ids import new_ulid
from mde_core.logging import get_logger
from mde_flow.service import FlowService
from mde_persistence.worker import JobHandler, JobWorker

log = get_logger("mde_context_api.worker")


def make_job_handler(flow: FlowService, worker_id: str) -> JobHandler:
    """``JobHandler`` that runs one claimed plan job through the flow controller."""

    async def handle(job: JobRecord, worker_token: CancellationToken) -> None:
        flow_token = flow.runs.token(job.id)

        async def join() -> None:
            await worker_token.wait()
            flow_token.cancel(worker_token.reason or "cancelled")

        bridge = asyncio.create_task(join())
        try:
            await flow.run_plan_job(job, worker_id, flow_token)
        finally:
            bridge.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge

    return handle


class BackgroundWorker:
    """A ``JobWorker`` loop as a task that starts and stops with the application."""

    def __init__(
        self,
        store: Store,
        flow: FlowService,
        settings: ApiSettings,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.worker_id = worker_id or f"api-{os.getpid()}-{new_ulid()[-6:].lower()}"
        self._worker = JobWorker(
            store,
            make_job_handler(flow, self.worker_id),
            self.worker_id,
            poll_interval=settings.worker_poll_seconds,
            heartbeat_interval=settings.worker_heartbeat_seconds,
            cancel_poll_interval=min(settings.worker_poll_seconds, 1.0),
            recover_after=settings.worker_recover_after_seconds,
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop_timeout = settings.worker_stop_timeout_seconds

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker.run_forever(self._stop), name=self.worker_id)
        log.info("worker_started", worker_id=self.worker_id)

    async def stop(self) -> None:
        """Ask the loop to end, let a running job finish (bounded), then cancel what is left."""
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_timeout)
        except TimeoutError:
            log.warning("worker_stop_timeout", worker_id=self.worker_id)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            task.cancel()
            raise
        log.info("worker_stopped", worker_id=self.worker_id)


# --------------------------------------------------------------------------- python -m ...worker


async def _serve(settings: ApiSettings, *, fake_builder: bool) -> None:
    from mde_context_api.composition import build_app_state

    state = await build_app_state(settings, fake_builder=fake_builder)
    worker = BackgroundWorker(state.store, state.flow, settings)
    worker.start()
    try:
        await asyncio.Event().wait()  # until cancelled (Ctrl-C, SIGTERM through asyncio.run)
    finally:
        await worker.stop()
        await state.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mde_context_api.worker", description="Run the plan-job worker."
    )
    parser.add_argument(
        "--fake-builder",
        action="store_true",
        help="use the scripted Spec Builder (dev only; same as SPEC_BUILDER=fake)",
    )
    args = parser.parse_args(argv)
    settings = load_api_settings()
    with contextlib.suppress(KeyboardInterrupt):  # interactive stop
        asyncio.run(
            _serve(settings, fake_builder=args.fake_builder),
            loop_factory=_loop.selector_loop_factory,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
