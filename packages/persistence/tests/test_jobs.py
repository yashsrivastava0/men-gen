"""The Postgres job queue: claim, heartbeat, recovery, retries, cancellation, concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from mde_contracts.records import JobRecord, PlanJobPayload
from mde_core.clock import FakeClock
from mde_core.errors import Conflict, NotFound
from mde_persistence import testing_factories as f
from mde_persistence.store import PostgresStore

pytestmark = pytest.mark.integration

A = f.TENANT_A
StoreMaker = Callable[..., PostgresStore]


async def _session(store: PostgresStore, tenant: str = A) -> str:
    async with store.begin(tenant) as uow:
        return (await uow.sessions.create(f.new_session(tenant_id=tenant))).id


async def _enqueue(store: PostgresStore, session_id: str, tenant: str = A) -> JobRecord:
    async with store.begin(tenant) as uow:
        return await uow.jobs.enqueue_plan(session_id, tenant, f.plan_payload())


async def _claim(store: PostgresStore, worker: str = "w1") -> JobRecord | None:
    async with store.begin(None) as uow:
        return await uow.jobs.claim(worker)


async def _get(store: PostgresStore, job_id: str, tenant: str = A) -> JobRecord:
    async with store.begin(tenant) as uow:
        job = await uow.jobs.get(job_id)
    assert job is not None
    return job


async def test_payload_round_trips_with_every_field(store: PostgresStore) -> None:
    sid = await _session(store)
    payload = PlanJobPayload(
        origin="HANDED_OFF",
        start_phase="build",
        spec_version=3,
        brief_version=2,
        affected_sections=["screens", "data"],
        rerun_jobs=["R1", "R3"],
        excluded_research_ids=["RA-001"],
        change_ops_json='[{"op": "add", "text": "café हिन्दी"}]',
    )
    async with store.begin(A) as uow:
        queued = await uow.jobs.enqueue_plan(sid, A, payload)
    claimed = await _claim(store)
    assert queued.payload == payload and claimed is not None and claimed.payload == payload
    assert claimed.session_id == sid and claimed.tenant_id == A and claimed.kind == "plan"


async def test_enqueue_for_unknown_session_is_not_found(store: PostgresStore) -> None:
    async with store.begin(A) as uow:
        with pytest.raises(NotFound):
            await uow.jobs.enqueue_plan("ses_NOSUCH", A, f.plan_payload())


async def test_a_losing_enqueue_does_not_poison_the_transaction(store: PostgresStore) -> None:
    sid = await _session(store)
    async with store.begin(A) as uow:
        await uow.jobs.enqueue_plan(sid, A, f.plan_payload())
        with pytest.raises(Conflict):
            await uow.jobs.enqueue_plan(sid, A, f.plan_payload())
        # the same unit of work keeps working (no aborted-transaction state)
        assert await uow.sessions.get(sid) is not None
        assert (await uow.jobs.active_for_session(sid)) is not None


async def test_concurrent_enqueues_on_several_sessions(store: PostgresStore) -> None:
    sessions = [await _session(store) for _ in range(4)]

    async def enqueue(sid: str) -> bool:
        async with store.begin(A) as uow:
            try:
                await uow.jobs.enqueue_plan(sid, A, f.plan_payload())
            except Conflict:
                return False
            return True

    results = await asyncio.gather(*(enqueue(sid) for sid in sessions for _ in range(5)))
    assert results.count(True) == 4 and results.count(False) == 16
    for sid in sessions:
        async with store.begin(A) as uow:
            assert await uow.jobs.active_for_session(sid) is not None


async def test_many_concurrent_claimers_get_each_job_exactly_once(store: PostgresStore) -> None:
    queued = {(await _enqueue(store, await _session(store))).id for _ in range(20)}

    async def worker(n: int) -> list[str]:
        mine: list[str] = []
        while (job := await _claim(store, f"worker-{n}")) is not None:
            assert job.lock_owner == f"worker-{n}" and job.state == "running"
            mine.append(job.id)
        return mine

    claimed = [
        job_id for batch in await asyncio.gather(*(worker(n) for n in range(8))) for job_id in batch
    ]
    assert sorted(claimed) == sorted(queued), "a job was lost or claimed twice"


async def test_claim_skips_rows_locked_by_another_transaction(store: PostgresStore) -> None:
    first = await _enqueue(store, await _session(store))
    second = await _enqueue(store, await _session(store))
    async with store.begin(None) as holder:
        held = await holder.jobs.claim("holder")  # row stays locked until this block ends
        assert held is not None and held.id == first.id
        other = await asyncio.wait_for(_claim(store, "other"), timeout=10)  # must not block
        assert other is not None and other.id == second.id


async def test_recovery_uses_the_injected_clock(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    sid = await _session(store)
    job = await _enqueue(store, sid)
    claimed = await _claim(store)
    assert claimed is not None and claimed.heartbeat_at == fake_clock.now()

    async def recover(age: float) -> int:
        async with store.begin(None) as uow:
            return await uow.jobs.recover_stuck(older_than_seconds=age)

    fake_clock.advance(30)
    async with store.begin(A) as uow:
        await uow.jobs.heartbeat(job.id, "w1")  # a live worker keeps the job
    fake_clock.advance(59)
    assert await recover(60) == 0  # last beat 59 s ago
    beat = await _get(store, job.id)
    assert beat.heartbeat_at is not None and beat.state == "running"
    fake_clock.advance(2)
    assert await recover(60) == 1  # 61 s of silence: the worker is gone
    requeued = await _get(store, job.id)
    assert requeued.state == "queued" and requeued.lock_owner is None and requeued.attempts == 1
    assert requeued.error is not None and "heartbeat" in requeued.error
    again = await _claim(store, "w2")
    assert again is not None and again.id == job.id and again.attempts == 2
    assert again.heartbeat_at == fake_clock.now()


async def test_recovery_fails_a_job_that_is_out_of_attempts(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock, max_attempts=2)
    job = await _enqueue(store, await _session(store))
    assert job.max_attempts == 2
    for expected in ("queued", "failed"):
        assert await _claim(store) is not None
        fake_clock.advance(600)
        async with store.begin(None) as uow:
            assert await uow.jobs.recover_stuck(older_than_seconds=60) == 1
        assert (await _get(store, job.id)).state == expected
    assert await _claim(store) is None
    async with store.begin(A) as uow:
        assert await uow.jobs.active_for_session(job.session_id) is None
    failed = await _get(store, job.id)
    assert failed.error is not None and "no attempts left" in failed.error


async def test_recovery_cancels_a_job_whose_cancel_was_pending(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    sid = await _session(store)
    job = await _enqueue(store, sid)
    await _claim(store)
    async with store.begin(A) as uow:
        assert await uow.jobs.cancel(sid) is True
    fake_clock.advance(600)
    async with store.begin(None) as uow:
        assert await uow.jobs.recover_stuck(older_than_seconds=60) == 1
    assert (await _get(store, job.id)).state == "cancelled"


async def test_error_retries_stop_at_max_attempts(make_store: StoreMaker) -> None:
    store = make_store(max_attempts=2)
    job = await _enqueue(store, await _session(store))
    for attempt, expected in ((1, "queued"), (2, "failed")):
        claimed = await _claim(store)
        assert claimed is not None and claimed.attempts == attempt
        async with store.begin(A) as uow:
            await uow.jobs.complete(job.id, error=f"attempt {attempt} blew up")
        stored = await _get(store, job.id)
        assert stored.state == expected and stored.error == f"attempt {attempt} blew up"
    assert await _claim(store) is None


async def test_success_clears_the_error_and_cannot_be_repeated(store: PostgresStore) -> None:
    job = await _enqueue(store, await _session(store))
    await _claim(store)
    async with store.begin(A) as uow:
        await uow.jobs.complete(job.id, error="flaky")
    await _claim(store)
    async with store.begin(A) as uow:
        await uow.jobs.complete(job.id)
        with pytest.raises(Conflict):
            await uow.jobs.complete(job.id)  # already finished
        with pytest.raises(Conflict):
            await uow.jobs.set_phase(job.id, "build")
        with pytest.raises(Conflict):
            await uow.jobs.heartbeat(job.id, "w1")
    done = await _get(store, job.id)
    assert done.state == "succeeded" and done.error is None and done.attempts == 2


async def test_completing_a_queued_job_is_refused(store: PostgresStore) -> None:
    job = await _enqueue(store, await _session(store))
    async with store.begin(A) as uow:
        with pytest.raises(Conflict):
            await uow.jobs.complete(job.id)


async def test_error_after_a_cancel_request_ends_cancelled_without_retry(
    store: PostgresStore,
) -> None:
    sid = await _session(store)
    job = await _enqueue(store, sid)
    await _claim(store)
    async with store.begin(A) as uow:
        await uow.jobs.cancel(sid)
        await uow.jobs.complete(job.id, error="stopped on request")
    stopped = await _get(store, job.id)
    assert stopped.state == "cancelled" and stopped.error == "stopped on request"
    assert await _claim(store) is None
    async with store.begin(A) as uow:
        await uow.jobs.enqueue_plan(sid, A, f.plan_payload())  # the session may run again


async def test_cancel_racing_a_claim_never_loses_the_request(store: PostgresStore) -> None:
    for _ in range(5):
        sid = await _session(store)
        job = await _enqueue(store, sid)

        async def cancel(session_id: str = sid) -> bool:
            async with store.begin(A) as uow:
                return await uow.jobs.cancel(session_id)

        cancelled, claimed = await asyncio.gather(cancel(), _claim(store))
        assert cancelled is True
        stored = await _get(store, job.id)
        assert stored.cancel_requested is True
        if claimed is None:  # cancel won: outright cancel
            assert stored.state == "cancelled"
        else:  # claim won: cooperative cancel, the worker will see the flag
            assert stored.state == "running"
        if claimed is not None:  # tidy: free the slot for the next round
            async with store.begin(A) as uow:
                await uow.jobs.complete(job.id)


async def test_heartbeat_requires_the_lock_owner(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    job = await _enqueue(store, await _session(store))
    await _claim(store, "owner")
    async with store.begin(A) as uow:
        with pytest.raises(Conflict):
            await uow.jobs.heartbeat(job.id, "intruder")
        await uow.jobs.heartbeat(job.id, "owner")
    fake_clock.advance(600)
    async with store.begin(None) as uow:
        await uow.jobs.recover_stuck(older_than_seconds=60)
    async with store.begin(A) as uow:
        with pytest.raises(Conflict):  # the owner lost the job when it was recovered
            await uow.jobs.heartbeat(job.id, "owner")
        with pytest.raises(NotFound):
            await uow.jobs.heartbeat("job_NOSUCH", "owner")


async def test_phase_is_kept_across_retries(store: PostgresStore) -> None:
    job = await _enqueue(store, await _session(store))
    await _claim(store)
    async with store.begin(A) as uow:
        await uow.jobs.set_phase(job.id, "research")
        await uow.jobs.set_phase(job.id, "build")
        await uow.jobs.complete(job.id, error="crashed in build")
    retry = await _claim(store, "w2")
    assert retry is not None and retry.phase == "build" and retry.attempts == 2


async def test_active_for_session_and_job_lookup(store: PostgresStore) -> None:
    sid = await _session(store)
    async with store.begin(A) as uow:
        assert await uow.jobs.active_for_session(sid) is None
        assert await uow.jobs.get("job_NOSUCH") is None
    job = await _enqueue(store, sid)
    async with store.begin(A) as uow:
        active = await uow.jobs.active_for_session(sid)
        assert active is not None and active.id == job.id
        assert (await uow.jobs.get(job.id)) == active
        assert active.created_at is not None and active.updated_at is not None
        assert active.created_at.utcoffset() is not None
