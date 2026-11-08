"""RunManager: one active run, cooperative cancellation, the planning queue."""

from __future__ import annotations

import pytest

from mde_contracts.records import JobRecord, PlanJobPayload, SessionRecord
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import CallCancelled, Conflict
from mde_flow.runs import MAX_QUEUED_MESSAGES, RunManager
from mde_flow.testing.store import InMemoryStore

PAYLOAD = PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1)


async def seeded() -> tuple[InMemoryStore, RunManager]:
    store = InMemoryStore(clock=FakeClock())
    async with store.begin("t") as uow:
        await uow.sessions.create(
            SessionRecord(id="ses_1", tenant_id="t", owner_id="u", app_id="app_1")
        )
    return store, RunManager()


async def test_start_run_enforces_one_active_run() -> None:
    store, runs = await seeded()
    async with store.begin("t") as uow:
        job = await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
        assert job.state == "queued" and not runs.token(job.id).cancelled
        with pytest.raises(Conflict):
            await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)


async def test_a_cancelled_but_unstarted_run_is_retired_so_a_new_one_can_start() -> None:
    store, runs = await seeded()
    async with store.begin("t") as uow:
        first = await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
        out = await runs.cancel_run(uow, "ses_1")
        assert out.cancelled and out.job_id == first.id and not out.was_started
        second = await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
        assert second.id != first.id
        old = await uow.jobs.get(first.id)
        assert old is not None and old.state == "cancelled"


async def test_a_running_run_that_is_winding_down_blocks_a_new_one() -> None:
    store, runs = await seeded()
    async with store.begin("t") as uow:
        await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
    async with store.begin(None) as system:
        claimed = await system.jobs.claim("w")
        assert claimed is not None
    async with store.begin("t") as uow:
        out = await runs.cancel_run(uow, "ses_1")
        assert out.cancelled and out.was_started  # a worker owns it: it winds down on its own
        with pytest.raises(Conflict) as info:
            await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
        assert info.value.details["cancel_requested"] is True


async def test_cancel_with_no_active_run() -> None:
    store, runs = await seeded()
    async with store.begin("t") as uow:
        assert (await runs.cancel_run(uow, "ses_1")).cancelled is False


async def test_the_in_process_guard_blocks_a_concurrent_start() -> None:
    store, runs = await seeded()
    runs._starting.add("ses_1")
    async with store.begin("t") as uow:
        with pytest.raises(Conflict):
            await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)


async def test_checkpoint_raises_when_cancelled_by_token_or_by_database_flag() -> None:
    store, runs = await seeded()
    async with store.begin("t") as uow:
        job = await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
    async with store.begin(None) as system:
        claimed = await system.jobs.claim("w")
        assert claimed is not None
    token = CancellationToken()
    await runs.checkpoint(store, claimed, token, "w")  # fine, and it heartbeats
    runs.token(job.id)  # the worker that runs a job tracks its token (run_next_job does this)
    runs.cancel_token(job.id)
    runs.cancel_token("job_unknown")  # cancelling a run nobody tracks is a no-op, not an entry
    assert "job_unknown" not in runs._tokens
    with pytest.raises(CallCancelled):
        await runs.checkpoint(store, claimed, runs.token(job.id), "w")
    other = CancellationToken()  # a worker in another process: only the database flag reaches it
    async with store.begin("t") as uow:
        await uow.jobs.cancel("ses_1")
    with pytest.raises(CallCancelled):
        await runs.checkpoint(store, claimed, other, "w")
    assert other.cancelled
    runs.forget(job.id)


async def claimed_job(store: InMemoryStore, runs: RunManager, worker: str = "w") -> JobRecord:
    async with store.begin("t") as uow:
        await runs.start_run(uow, session_id="ses_1", tenant_id="t", payload=PAYLOAD)
    async with store.begin(None) as system:
        job = await system.jobs.claim(worker)
    assert job is not None
    return job


async def test_finish_ends_the_run_for_good_even_though_complete_would_requeue_it() -> None:
    store, runs = await seeded()
    job = await claimed_job(store, runs)
    async with store.begin("t") as uow:
        assert await runs.finish(uow, job, "w", error="failed") is True
    async with store.begin("t") as uow:
        ended = await uow.jobs.get(job.id)
        assert ended is not None and ended.state == "cancelled" and ended.error == "failed"
        assert await uow.jobs.active_for_session("ses_1") is None  # the next run may start
    async with store.begin(None) as system:
        assert await system.jobs.claim("w2") is None  # and nobody picks the old one up again


async def test_finish_leaves_a_job_this_worker_no_longer_owns_alone() -> None:
    store, runs = await seeded()
    job = await claimed_job(store, runs, "w")
    async with store.begin("t") as uow:
        assert await runs.finish(uow, job, "someone-else", error="failed") is False
        current = await uow.jobs.get(job.id)
        assert current is not None and current.state == "running" and not current.cancel_requested
    store.clock.advance(3600)  # type: ignore[attr-defined]
    async with store.begin(None) as system:  # the worker died: recovery requeues the job
        assert await system.jobs.recover_stuck(older_than_seconds=60) == 1
    async with store.begin("t") as uow:
        assert await runs.finish(uow, job, "w", error="late") is False  # it lost the job
        requeued = await uow.jobs.get(job.id)
        assert requeued is not None and requeued.state == "queued"


async def test_checkpoint_treats_a_lost_job_as_a_cancellation() -> None:
    store, runs = await seeded()
    job = await claimed_job(store, runs, "w")
    store.clock.advance(3600)  # type: ignore[attr-defined]
    async with store.begin(None) as system:
        await system.jobs.recover_stuck(older_than_seconds=60)  # w lost its lock
    token = CancellationToken()
    with pytest.raises(CallCancelled):
        await runs.checkpoint(store, job, token, "w")
    assert token.cancelled and token.reason == "lost the job"


def test_session_lock_is_per_session_and_tokens_are_per_job() -> None:
    runs = RunManager()
    held = runs.session_lock("a")
    assert held is runs.session_lock("a")
    assert held is not runs.session_lock("b")
    assert runs.token("j1") is runs.token("j1") and runs.token("j1") is not runs.token("j2")


def test_queue_message_redacts_secrets_caps_and_ignores_blanks() -> None:
    rec = SessionRecord(id="s", tenant_id="t", owner_id="u", app_id="a")
    rec2, ok = RunManager.queue_message(rec, "  make it red  ")
    assert ok and rec2.queued_messages == ["make it red"]
    rec3, _ = RunManager.queue_message(rec2, "key sk-" + "z" * 40)
    assert "zzzz" not in rec3.queued_messages[1] and "[REDACTED]" in rec3.queued_messages[1]
    same, ok = RunManager.queue_message(rec3, "   ")
    assert ok and same is rec3
    full = rec3.model_copy(update={"queued_messages": ["x"] * MAX_QUEUED_MESSAGES})
    out, ok = RunManager.queue_message(full, "one more")
    assert not ok and out is full


def test_unused_session_locks_are_dropped() -> None:
    import gc

    runs = RunManager()
    runs.session_lock("gone")
    gc.collect()
    assert len(runs._locks) == 0
