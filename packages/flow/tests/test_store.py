"""``InMemoryStore`` keeps the semantics documented in ``mde_contracts.ports`` (it is what the flow
is tested against, so it must behave like the Postgres implementation).

The shared rules (tenancy, system scope, versions, the job queue, ...) are pinned by the persistence
contract suite, which runs against this fake in ``tests/integration/test_inmemory_store_contract.py``.
This module covers what is specific to the fake: row locks and transaction boundaries, rollback,
the JSON round trip, and the unit-of-work counters the transaction tests rely on.
"""

from __future__ import annotations

import asyncio

import pytest

from mde_contracts.brief import Brief
from mde_contracts.events import AssistantMessage, UserMessage
from mde_contracts.gates import ApprovalRecord
from mde_contracts.records import (
    BriefVersionRecord,
    PlanJobPayload,
    ResearchNoteRecord,
    SessionRecord,
    SpecVersionRecord,
)
from mde_contracts.research import ResearchNote
from mde_core.clock import FakeClock
from mde_core.errors import Conflict, NotFound, TenantViolation, ValidationFailed
from mde_core.hashing import sha256_hex
from mde_flow.testing.builders import make_brief, make_plan, make_spec, render_spec_text
from mde_flow.testing.store import InMemoryStore


def session(sid: str = "ses_1", tenant: str = "t1", owner: str = "u1") -> SessionRecord:
    return SessionRecord(id=sid, tenant_id=tenant, owner_id=owner, app_id="app_1")


def spec_rec(version: int = 1, sid: str = "ses_1", tenant: str = "t1") -> SpecVersionRecord:
    spec = make_spec(version=version)
    text = render_spec_text(spec)
    digest = sha256_hex(text)
    return SpecVersionRecord(
        session_id=sid, tenant_id=tenant, version=version, hash=digest, spec_text=text, spec=spec,
        plan=make_plan(spec, spec_hash=digest), brief_version=1,
    )  # fmt: skip


def msg(i: int, *, reply_to: str | None = None, sid: str = "ses_1") -> AssistantMessage:
    return AssistantMessage(event_id=f"evt_{i}", session_id=sid, text="hi", in_reply_to=reply_to)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(clock=FakeClock())


async def seed(store: InMemoryStore, sid: str = "ses_1", tenant: str = "t1") -> SessionRecord:
    async with store.begin(tenant) as uow:
        return await uow.sessions.create(session(sid, tenant))


async def test_session_cas_and_version_counter(store: InMemoryStore) -> None:
    rec = await seed(store)
    async with store.begin("t1") as uow:
        got = await uow.sessions.get("ses_1")
        assert got is not None and got.version == 0
        saved = await uow.sessions.save(got.model_copy(update={"title": "x"}), expected_version=0)
        assert saved.version == 1 and saved.updated_at is not None
        with pytest.raises(Conflict):
            await uow.sessions.save(got, expected_version=0)  # stale
    assert rec.created_at is not None


async def test_session_create_twice_conflicts(store: InMemoryStore) -> None:
    await seed(store)
    with pytest.raises(Conflict):
        await seed(store)


async def test_tenant_isolation_everywhere(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        await uow.events.record_inbound("ses_1", UserMessage(event_id="e1", text="a"))
        await uow.events.append_outbound("ses_1", msg(1))
        await uow.briefs.add(
            BriefVersionRecord(session_id="ses_1", tenant_id="t1", brief=make_brief(1), slots={})
        )
        await uow.specs.add(spec_rec())
        await uow.jobs.enqueue_plan(
            "ses_1", "t1", PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1)
        )
    async with store.begin("t2") as other:
        assert await other.sessions.get("ses_1") is None
        assert await other.sessions.list_for_owner("u1") == []
        assert await other.events.list_outbound("ses_1") == []
        assert await other.events.replies_to("ses_1", "e1") == []
        assert (
            await other.briefs.get("ses_1", 1) is None
            and await other.briefs.latest("ses_1") is None
        )
        assert (
            await other.specs.get("ses_1", 1) is None
            and await other.specs.list_versions("ses_1") == []
        )
        assert await other.jobs.active_for_session("ses_1") is None
        assert await other.research.latest("ses_1") is None
        assert await other.approvals.list("ses_1", "brief") == []
        assert await other.jobs.cancel("ses_1") is False
        with pytest.raises(NotFound):
            await other.events.record_inbound("ses_1", UserMessage(event_id="e2", text="a"))
        with pytest.raises(NotFound):
            await other.events.append_outbound("ses_1", msg(2))
        with pytest.raises(TenantViolation):
            await other.sessions.create(session("ses_2", "t1"))
        with pytest.raises(NotFound):
            await other.jobs.enqueue_plan(
                "ses_1",
                "t2",
                PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1),
            )
    async with store.begin(None) as system:  # system scope is for the queue only
        with pytest.raises(TenantViolation):
            await system.sessions.get("ses_1")


async def test_inbound_events_are_idempotent_and_outbound_seq_increases(
    store: InMemoryStore,
) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        ev = UserMessage(event_id="e1", text="hello")
        assert await uow.events.record_inbound("ses_1", ev) is True
        assert await uow.events.record_inbound("ses_1", ev) is False
        a = await uow.events.append_outbound("ses_1", msg(1, reply_to="e1"))
        b = await uow.events.append_outbound("ses_1", msg(2, reply_to="e1"))
        c = await uow.events.append_outbound("ses_1", msg(3))
        assert [a.seq, b.seq, c.seq] == [1, 2, 3]
        assert [e.event_id for e in await uow.events.replies_to("ses_1", "e1")] == [
            "evt_1",
            "evt_2",
        ]
        assert [e.seq for e in await uow.events.list_outbound("ses_1", after_seq=1, limit=1)] == [2]


async def test_a_failed_unit_of_work_rolls_everything_back(store: InMemoryStore) -> None:
    await seed(store)

    async def boom() -> None:
        async with store.begin("t1") as uow:
            await uow.events.record_inbound("ses_1", UserMessage(event_id="e1", text="a"))
            await uow.events.append_outbound("ses_1", msg(1))
            rec = await uow.sessions.get("ses_1")
            assert rec is not None
            await uow.sessions.save(rec.model_copy(update={"title": "dirty"}), expected_version=0)
            await uow.specs.add(spec_rec())
            raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        await boom()
    async with store.begin("t1") as uow:
        rec = await uow.sessions.get("ses_1")
        assert rec is not None and rec.title is None and rec.version == 0
        assert (
            await uow.events.record_inbound("ses_1", UserMessage(event_id="e1", text="a")) is True
        )
        assert await uow.events.list_outbound("ses_1") == []
        assert await uow.specs.latest("ses_1") is None


async def test_for_update_serialises_units_of_work_on_a_session(store: InMemoryStore) -> None:
    await seed(store)
    order: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with store.begin("t1") as uow:
            await uow.sessions.get("ses_1", for_update=True)
            order.append("first-locked")
            entered.set()
            await release.wait()
            order.append("first-done")

    async def second() -> None:
        await entered.wait()
        async with store.begin("t1") as uow:
            await uow.sessions.get("ses_1", for_update=True)
            order.append("second-locked")

    t1, t2 = asyncio.create_task(first()), asyncio.create_task(second())
    for _ in range(5):
        await asyncio.sleep(0)
    assert order == ["first-locked"]  # the second is waiting on the row lock
    release.set()
    await asyncio.gather(t1, t2)
    assert order == ["first-locked", "first-done", "second-locked"]


async def test_versions_are_immutable_except_status_and_git_sha(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        await uow.specs.add(spec_rec(1))
        with pytest.raises(Conflict):
            await uow.specs.add(spec_rec(1))  # the same version number can never be written twice
        a = await uow.specs.set_status("ses_1", 1, "approved", approved_at=store.clock.now())
        assert a.status == "approved" and a.approved_at is not None
        with pytest.raises(Conflict):
            await uow.specs.set_status("ses_1", 1, "draft")  # never backwards
        h = await uow.specs.set_status("ses_1", 1, "handed_off", git_sha="abc")
        assert h.git_sha == "abc" and h.hash == a.hash and h.spec_text == a.spec_text
        with pytest.raises(Conflict):
            await uow.specs.set_status("ses_1", 1, "handed_off", git_sha="other")
        again = await uow.specs.set_status("ses_1", 1, "handed_off", git_sha="abc")
        assert again.git_sha == "abc"
        with pytest.raises(Conflict):  # a handed-off version is history: never superseded
            await uow.specs.set_status("ses_1", 1, "superseded")
        with pytest.raises(Conflict):
            await uow.specs.set_status("ses_1", 1, "approved")
        with pytest.raises(NotFound):
            await uow.specs.set_status("ses_1", 9, "approved")


async def test_spec_listing_latest_and_get(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        for v in (2, 1, 3):
            await uow.specs.add(spec_rec(v))
        assert [r.version for r in await uow.specs.list_versions("ses_1")] == [1, 2, 3]
        latest = await uow.specs.latest("ses_1")
        assert latest is not None and latest.version == 3
        assert (await uow.specs.get("ses_1", 2)) is not None
        assert await uow.specs.get("ses_1", 7) is None


async def test_brief_versions_and_status_only_move_forward(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        for v in (1, 2):
            await uow.briefs.add(
                BriefVersionRecord(
                    session_id="ses_1", tenant_id="t1", brief=make_brief(v), slots={}
                )
            )
        with pytest.raises(Conflict):
            await uow.briefs.add(
                BriefVersionRecord(
                    session_id="ses_1", tenant_id="t1", brief=make_brief(2), slots={}
                )
            )
        latest = await uow.briefs.latest("ses_1")
        assert latest is not None and latest.brief.version == 2
        await uow.briefs.set_status("ses_1", 1, "approved")
        await uow.briefs.set_status("ses_1", 1, "approved")  # same status is fine
        got = await uow.briefs.get("ses_1", 1)
        assert got is not None and got.brief.status == "approved"
        assert got.brief.compute_hash() == got.brief.hash  # status is not part of the hash
        await uow.briefs.set_status("ses_1", 1, "superseded")
        with pytest.raises(Conflict):
            await uow.briefs.set_status("ses_1", 1, "approved")
        with pytest.raises(NotFound):
            await uow.briefs.set_status("ses_1", 5, "approved")


async def test_research_notes_keep_history_and_latest(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        assert await uow.research.latest("ses_1") is None
        for v in (1, 2):
            note = ResearchNote(brief_hash="b", version=v).with_hash()
            await uow.research.add(
                ResearchNoteRecord(session_id="ses_1", tenant_id="t1", note=note)
            )
        latest = await uow.research.latest("ses_1")
        assert latest is not None and latest.note.version == 2


async def test_approvals_filter_and_keep_insertion_order(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        for i, (gate, ver, digest) in enumerate(
            [("brief", 1, "a"), ("plan", 1, "b"), ("plan", 2, "c"), ("plan", 2, "c")]
        ):
            await uow.approvals.add(
                ApprovalRecord.model_validate(
                    {
                        "id": f"apr_{i}",
                        "session_id": "ses_1",
                        "gate": gate,
                        "version": ver,
                        "hash": digest,
                        "approver_id": "u",
                        "decision": "approve",
                    }
                )  # fmt: skip
            )
        assert [a.id for a in await uow.approvals.list("ses_1", "plan")] == [
            "apr_1",
            "apr_2",
            "apr_3",
        ]
        assert [a.id for a in await uow.approvals.list("ses_1", "plan", version=2)] == [
            "apr_2",
            "apr_3",
        ]
        assert [a.id for a in await uow.approvals.list("ses_1", "plan", hash="b")] == ["apr_1"]
        stored = (await uow.approvals.list("ses_1", "brief"))[0]
        assert stored.at is not None


PAYLOAD = PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1)


async def test_one_active_run_per_session(store: InMemoryStore) -> None:
    await seed(store)
    await seed(store, "ses_2")
    async with store.begin("t1") as uow:
        job = await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)
        with pytest.raises(Conflict):
            await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)
        other = await uow.jobs.enqueue_plan("ses_2", "t1", PAYLOAD)  # another session is fine
        assert other.id != job.id
        active = await uow.jobs.active_for_session("ses_1")
        assert active is not None and active.id == job.id
        with pytest.raises(Conflict):  # only a running job can be completed
            await uow.jobs.complete(job.id)
        assert await uow.jobs.cancel("ses_1") is True  # a queued job is cancelled outright
        assert await uow.jobs.active_for_session("ses_1") is None
        again = await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)  # allowed once it ended
        assert again.id != job.id


async def test_job_lifecycle_claim_heartbeat_cancel_recover(store: InMemoryStore) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        job = await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)
        with pytest.raises(TenantViolation):
            await uow.jobs.claim("w1")  # system scope only
        with pytest.raises(TenantViolation):
            await uow.jobs.recover_stuck(older_than_seconds=1)
    async with store.begin(None) as sys_uow:
        claimed = await sys_uow.jobs.claim("w1")
        assert claimed is not None and claimed.state == "running" and claimed.attempts == 1
        assert await sys_uow.jobs.claim("w2") is None
    async with store.begin("t1") as uow:
        await uow.jobs.set_phase(job.id, "build")
        await uow.jobs.heartbeat(job.id, "w1")
        with pytest.raises(Conflict):
            await uow.jobs.heartbeat(job.id, "w2")  # not its lock owner
        assert await uow.jobs.is_cancel_requested(job.id) is False
        assert await uow.jobs.cancel("ses_1") is True  # running: flagged, not stopped
        assert await uow.jobs.is_cancel_requested(job.id) is True
    store.clock.advance(120)  # type: ignore[attr-defined]
    async with store.begin(None) as sys_uow:
        assert await sys_uow.jobs.recover_stuck(older_than_seconds=60) == 1
    async with store.begin("t1") as uow:
        done = await uow.jobs.get(job.id)
        assert done is not None and done.state == "cancelled" and done.phase == "build"
        assert done.lock_owner is None and await uow.jobs.cancel("ses_1") is False
        with pytest.raises(Conflict):
            await uow.jobs.complete(job.id)  # no longer running
        with pytest.raises(Conflict):
            await uow.jobs.heartbeat(job.id, "w1")  # the lock is gone
        with pytest.raises(NotFound):
            await uow.jobs.complete("job_missing")
        with pytest.raises(NotFound):
            await uow.jobs.heartbeat("job_missing", "w")
        with pytest.raises(NotFound):
            await uow.jobs.set_phase("job_missing", "build")


async def test_a_failed_attempt_retries_then_fails_and_a_cancelled_one_does_not(
    store: InMemoryStore,
) -> None:
    await seed(store)
    async with store.begin("t1") as uow:
        job = await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)
    for attempt in (1, 2, 3):
        async with store.begin(None) as sys_uow:
            claimed = await sys_uow.jobs.claim("w")
            assert claimed is not None and claimed.attempts == attempt
        async with store.begin("t1") as uow:
            await uow.jobs.complete(job.id, error=f"boom {attempt}")
            got = await uow.jobs.get(job.id)
            assert got is not None and got.error == f"boom {attempt}"
            assert got.state == ("queued" if attempt < 3 else "failed")
    async with store.begin("t1") as uow:
        second = await uow.jobs.enqueue_plan("ses_1", "t1", PAYLOAD)
    async with store.begin(None) as sys_uow:
        assert await sys_uow.jobs.claim("w") is not None
    async with store.begin("t1") as uow:
        await uow.jobs.cancel("ses_1")
        await uow.jobs.complete(second.id, error="stopped")  # cancelled: never retried
        done = await uow.jobs.get(second.id)
        assert done is not None and done.state == "cancelled" and done.error == "stopped"


async def test_commit_ends_the_transaction_and_releases_the_row_lock(
    store: InMemoryStore,
) -> None:
    await seed(store)
    events: list[str] = []

    async with store.begin("t1") as first:
        await first.sessions.get("ses_1", for_update=True)

        async def second() -> None:
            async with store.begin("t1") as other:
                await other.sessions.get("ses_1", for_update=True)
                events.append("second-locked")

        waiter = asyncio.create_task(second())
        for _ in range(3):
            await asyncio.sleep(0)
        assert events == []  # blocked behind the first unit of work
        await first.commit()  # Postgres: COMMIT ends the transaction and frees its row locks
        await waiter
        assert events == ["second-locked"]


async def test_save_waits_for_the_row_lock_and_then_sees_the_winner(store: InMemoryStore) -> None:
    await seed(store)
    holder_has_lock, release = asyncio.Event(), asyncio.Event()
    outcome: list[str] = []

    async def holder() -> None:
        async with store.begin("t1") as uow:
            got = await uow.sessions.get("ses_1", for_update=True)
            assert got is not None
            holder_has_lock.set()
            await release.wait()
            await uow.sessions.save(got.model_copy(update={"title": "holder"}), expected_version=0)

    async def loser() -> None:
        await holder_has_lock.wait()
        async with store.begin("t1") as uow:
            try:
                await uow.sessions.save(session(), expected_version=0)  # blocks on the row lock
            except Conflict:
                outcome.append("conflict")

    tasks = [asyncio.create_task(holder()), asyncio.create_task(loser())]
    for _ in range(5):
        await asyncio.sleep(0)
    assert outcome == []  # still waiting
    release.set()
    await asyncio.gather(*tasks)
    assert outcome == ["conflict"]


async def test_outbound_seq_is_rolled_back_with_the_transaction(store: InMemoryStore) -> None:
    await seed(store)

    async def boom() -> None:
        async with store.begin("t1") as uow:
            await uow.events.append_outbound("ses_1", msg(1))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await boom()
    async with store.begin("t1") as uow:
        first = await uow.events.append_outbound("ses_1", msg(2))
        assert first.seq == 1 and first.at is not None
        with pytest.raises(Conflict):  # the same event id twice in one session
            await uow.events.append_outbound("ses_1", msg(2))
        with pytest.raises(ValidationFailed):
            await uow.events.append_outbound("ses_other", msg(3))  # the event belongs to ses_1


async def test_open_units_count_the_connections_a_test_can_account_for(
    store: InMemoryStore,
) -> None:
    assert store.open_units == 0
    async with store.begin("t1"), store.begin("t1"):
        assert store.open_units == 2
    assert store.open_units == 0 and store.peak_open_units == 2

    async def boom() -> None:
        async with store.begin("t1"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await boom()
    assert store.open_units == 0  # also after an exception


async def test_records_survive_a_json_round_trip(store: InMemoryStore) -> None:
    """Stored as JSON and re-validated, like a database column."""
    await seed(store)
    async with store.begin("t1") as uow:
        brief: Brief = make_brief(1)
        await uow.briefs.add(
            BriefVersionRecord(session_id="ses_1", tenant_id="t1", brief=brief, slots={})
        )
        got = await uow.briefs.get("ses_1", 1)
        assert got is not None and got.brief == brief and got.brief is not brief
