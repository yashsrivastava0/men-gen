"""``JobWorker`` and ``InlineJobRunner``: heartbeat, cancellation bridge, retries, recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest

from mde_contracts.ports import UnitOfWork
from mde_contracts.records import JobRecord
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import CallCancelled
from mde_persistence import testing_factories as f
from mde_persistence.store import PostgresStore
from mde_persistence.worker import InlineJobRunner, JobWorker

pytestmark = pytest.mark.integration

A = f.TENANT_A
StoreMaker = Callable[..., PostgresStore]
FAST = {"poll_interval": 0.01, "heartbeat_interval": 0.01, "cancel_poll_interval": 0.01}


async def _session(store: PostgresStore, tenant: str = A) -> str:
    async with store.begin(tenant) as uow:
        return (await uow.sessions.create(f.new_session(tenant_id=tenant))).id


async def _enqueue(store: PostgresStore, session_id: str, tenant: str = A) -> JobRecord:
    async with store.begin(tenant) as uow:
        return await uow.jobs.enqueue_plan(session_id, tenant, f.plan_payload())


async def _get(store: PostgresStore, job_id: str, tenant: str = A) -> JobRecord:
    async with store.begin(tenant) as uow:
        job = await uow.jobs.get(job_id)
    assert job is not None
    return job


async def _until(predicate: Callable[[], Awaitable[bool]]) -> None:
    """Poll a condition that another task makes true (bounded; fails the test on timeout)."""
    async with asyncio.timeout(15):
        while not await predicate():  # noqa: ASYNC110  (database state: there is no event to await)
            await asyncio.sleep(0.01)


async def _noop(job: JobRecord, cancel: CancellationToken) -> None:
    return None


async def test_run_once_on_an_empty_queue(store: PostgresStore) -> None:
    worker = JobWorker(store, _noop, "w1", **FAST)
    assert await worker.run_once() is False


async def test_handler_success_completes_the_job(store: PostgresStore) -> None:
    sid = await _session(store)
    job = await _enqueue(store, sid)
    seen: list[tuple[str, str, int]] = []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        seen.append((j.id, j.lock_owner or "", j.attempts))
        assert not cancel.cancelled
        async with store.begin(j.tenant_id) as uow:  # what the flow does: tenant-bound work
            await uow.jobs.set_phase(j.id, "build")

    worker = JobWorker(store, handler, "worker-7", **FAST)
    assert await worker.run_once() is True
    assert seen == [(job.id, "worker-7", 1)]
    done = await _get(store, job.id)
    assert done.state == "succeeded" and done.phase == "build" and done.lock_owner is None
    assert await worker.run_once() is False


async def test_handler_errors_requeue_until_attempts_run_out(make_store: StoreMaker) -> None:
    store = make_store(max_attempts=3)
    job = await _enqueue(store, await _session(store))

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        raise RuntimeError(f"attempt {j.attempts} failed")

    worker = JobWorker(store, handler, "w1", **FAST)
    for attempt, state in ((1, "queued"), (2, "queued"), (3, "failed")):
        assert await worker.run_once() is True
        stored = await _get(store, job.id)
        assert stored.state == state and stored.attempts == attempt
        assert stored.error == f"RuntimeError: attempt {attempt} failed"
    assert await worker.run_once() is False


async def test_cancel_mid_run_stops_the_handler(store: PostgresStore) -> None:
    sid = await _session(store)
    job = await _enqueue(store, sid)
    started, reasons = asyncio.Event(), []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        started.set()
        await cancel.wait()  # a long step that ends when cancellation arrives
        reasons.append(cancel.reason)
        cancel.check()  # raises CallCancelled, as the engines do between steps

    run = asyncio.create_task(JobWorker(store, handler, "w1", **FAST).run_once())
    await asyncio.wait_for(started.wait(), 15)
    async with store.begin(A) as uow:
        assert await uow.jobs.cancel(sid) is True
    assert await asyncio.wait_for(run, 15) is True
    assert reasons == ["cancel_requested"]
    stopped = await _get(store, job.id)
    assert stopped.state == "cancelled" and stopped.cancel_requested is True
    async with store.begin(A) as uow:
        assert await uow.jobs.active_for_session(sid) is None


async def test_a_handler_that_returns_quietly_after_cancel_is_cancelled(
    store: PostgresStore,
) -> None:
    sid = await _session(store)
    job = await _enqueue(store, sid)
    started = asyncio.Event()

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        started.set()
        await cancel.wait()  # and simply returns

    run = asyncio.create_task(JobWorker(store, handler, "w1", **FAST).run_once())
    await asyncio.wait_for(started.wait(), 15)
    async with store.begin(A) as uow:
        await uow.jobs.cancel(sid)
    await asyncio.wait_for(run, 15)
    assert (await _get(store, job.id)).state == "cancelled"


async def test_handler_raising_call_cancelled_is_cancelled_not_retried(
    store: PostgresStore,
) -> None:
    sid = await _session(store)
    job = await _enqueue(store, sid)

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        async with store.begin(j.tenant_id) as uow:
            await uow.jobs.cancel(j.session_id)
        raise CallCancelled("stopped by the user")

    await JobWorker(store, handler, "w1", **FAST).run_once()
    stopped = await _get(store, job.id)
    assert stopped.state == "cancelled" and stopped.error == "stopped by the user"


async def test_worker_heartbeats_while_the_handler_runs(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    job = await _enqueue(store, await _session(store))
    claimed_at = fake_clock.now()
    observed: list[bool] = []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        fake_clock.advance(100)  # the handler "works" for 100 s of fake time

        async def beat_recorded() -> bool:
            return (await _get(store, j.id)).heartbeat_at == fake_clock.now()

        await _until(beat_recorded)
        observed.append(True)

    worker = JobWorker(store, handler, "w1", **FAST)
    await worker.run_once()
    assert observed == [True]
    assert (await _get(store, job.id)).state == "succeeded"
    assert fake_clock.now() > claimed_at


async def test_losing_the_lock_cancels_the_handler_and_skips_completion(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    job = await _enqueue(store, await _session(store))
    started, reasons = asyncio.Event(), []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        started.set()
        await cancel.wait()
        reasons.append(cancel.reason)

    run = asyncio.create_task(
        JobWorker(
            store,
            handler,
            "w1",
            poll_interval=0.01,
            heartbeat_interval=0.01,
            cancel_poll_interval=0.01,
        ).run_once()
    )
    await asyncio.wait_for(started.wait(), 15)
    fake_clock.advance(10_000)  # an ops sweep decides the worker is dead
    async with store.begin(None) as uow:
        assert await uow.jobs.recover_stuck(older_than_seconds=60) == 1
    await asyncio.wait_for(run, 15)
    assert reasons == ["lock_lost"]
    stored = await _get(store, job.id)
    assert stored.state == "queued" and stored.lock_owner is None  # not completed by the old worker


async def test_run_forever_works_through_the_queue_until_stopped(store: PostgresStore) -> None:
    jobs = [await _enqueue(store, await _session(store)) for _ in range(3)]
    handled: list[str] = []
    stop = asyncio.Event()

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        handled.append(j.id)
        if len(handled) == 3:
            stop.set()

    await asyncio.wait_for(JobWorker(store, handler, "w1", **FAST).run_forever(stop), 30)
    assert handled == [j.id for j in jobs]  # oldest first
    for j in jobs:
        assert (await _get(store, j.id)).state == "succeeded"


async def test_run_forever_returns_when_stopped_while_idle(store: PostgresStore) -> None:
    stop = asyncio.Event()
    run = asyncio.create_task(JobWorker(store, _noop, "w1", **FAST).run_forever(stop))
    await asyncio.sleep(0)  # let it enter its idle wait
    assert not run.done()
    stop.set()
    await asyncio.wait_for(run, 15)


async def test_an_idle_worker_recovers_jobs_whose_worker_died(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    job = await _enqueue(store, await _session(store))
    async with store.begin(None) as uow:
        assert await uow.jobs.claim("ghost-worker") is not None  # ... which then dies
    fake_clock.advance(3600)
    stop, handled = asyncio.Event(), []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        handled.append((j.id, j.attempts, j.lock_owner))
        stop.set()

    worker = JobWorker(store, handler, "w2", recover_after=60, **FAST)
    await asyncio.wait_for(worker.run_forever(stop), 30)
    assert handled == [(job.id, 2, "w2")]
    assert (await _get(store, job.id)).state == "succeeded"


async def test_inline_runner_runs_the_next_job_deterministically(store: PostgresStore) -> None:
    first = await _enqueue(store, await _session(store))
    second = await _enqueue(store, await _session(store))
    order: list[str] = []

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        order.append(j.id)

    runner = InlineJobRunner(store, handler)
    done = await runner.run_next()
    assert done is not None and done.id == first.id and done.state == "succeeded"
    assert (await _get(store, second.id)).state == "queued"  # nothing else ran
    rest = await runner.run_all()
    assert [j.id for j in rest] == [second.id] and order == [first.id, second.id]
    assert await runner.run_next() is None


async def test_inline_runner_retries_then_fails(make_store: StoreMaker) -> None:
    store = make_store(max_attempts=2)
    job = await _enqueue(store, await _session(store))

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        raise ValueError("nope")

    finished = await InlineJobRunner(store, handler).run_all(limit=10)
    assert [(j.state, j.attempts) for j in finished] == [("queued", 1), ("failed", 2)]
    assert finished[-1].id == job.id and finished[-1].error == "ValueError: nope"


async def test_inline_runner_records_a_handler_requested_cancel(store: PostgresStore) -> None:
    sid = await _session(store)
    await _enqueue(store, sid)

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        async with store.begin(j.tenant_id) as uow:
            await uow.jobs.cancel(sid)

    done = await InlineJobRunner(store, handler).run_next()
    assert done is not None and done.state == "cancelled"


async def test_inline_runner_ignores_jobs_cancelled_while_queued(store: PostgresStore) -> None:
    sid = await _session(store)
    await _enqueue(store, sid)
    async with store.begin(A) as uow:
        await uow.jobs.cancel(sid)
    assert await InlineJobRunner(store, _noop).run_next() is None


class _FlakyStore:
    """Wraps a real store; the first ``failures`` system-scope openings fail like a lost database."""

    def __init__(self, inner: PostgresStore, failures: int) -> None:
        self._inner = inner
        self.failures = failures

    def begin(self, tenant_id: str | None) -> AbstractAsyncContextManager[UnitOfWork]:
        if tenant_id is None and self.failures > 0:
            self.failures -= 1
            raise ConnectionError("database went away")
        return self._inner.begin(tenant_id)


async def test_run_forever_survives_database_errors(store: PostgresStore) -> None:
    job = await _enqueue(store, await _session(store))
    flaky = _FlakyStore(store, failures=2)
    stop = asyncio.Event()

    async def handler(j: JobRecord, cancel: CancellationToken) -> None:
        stop.set()

    await asyncio.wait_for(JobWorker(flaky, handler, "w1", **FAST).run_forever(stop), 30)
    assert flaky.failures == 0
    assert (await _get(store, job.id)).state == "succeeded"
