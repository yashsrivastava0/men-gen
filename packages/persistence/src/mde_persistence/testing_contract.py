"""Reusable store contract suite: the semantics every ``Store`` implementation must have.

``PostgresStore`` runs it in ``packages/persistence/tests``; the flow package's in-memory store
runs the SAME checks, so unit tests built on the fake and production code on Postgres agree.

Usage (pytest)::

    from mde_persistence.testing_contract import CHECKS

    @pytest.mark.parametrize("name", sorted(CHECKS))
    async def test_contract(name):
        await CHECKS[name](make_fresh_store)   # async callable returning an EMPTY Store

Each check calls the factory once (or more, for concurrency checks, only once as well) and uses only
the ``mde_contracts.ports`` protocols. Where the protocol leaves the exact error open (for
example a cross-tenant write may be ``TenantViolation`` or ``NotFound``) the checks accept both.
"""

# ruff: noqa: PT018  (compound asserts keep the contract checks readable)
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import TypeVar

from mde_contracts.ports import Store
from mde_contracts.records import SessionRecord
from mde_core.errors import Conflict, MdeError, NotFound, TenantViolation
from mde_persistence import testing_factories as f

StoreFactory = Callable[[], Awaitable[Store]]
Check = Callable[[StoreFactory], Awaitable[None]]

T = TypeVar("T")
CHECKS: dict[str, Check] = {}

A = f.TENANT_A
B = f.TENANT_B


def check(fn: Check) -> Check:
    CHECKS[fn.__name__.removeprefix("check_")] = fn
    return fn


async def _raises(
    expected: type[MdeError] | tuple[type[MdeError], ...], coro: Coroutine[object, object, object]
) -> MdeError:
    names = expected if isinstance(expected, tuple) else (expected,)
    try:
        await coro
    except MdeError as exc:
        caught = exc
    else:
        raise AssertionError(f"expected {names}, nothing was raised")
    assert isinstance(caught, names), f"expected {names}, got {type(caught).__name__}: {caught}"
    return caught


async def _empty_or_not_found(coro: Coroutine[object, object, list[T]]) -> None:
    """A list lookup for a foreign or unknown session: an empty list, or NotFound."""
    try:
        result = await coro
    except NotFound:
        return
    assert result == [], f"expected nothing, got {result!r}"


async def _new_session(
    store: Store, tenant: str = A, *, owner_id: str = "usr_owner", title: str | None = None
) -> SessionRecord:
    async with store.begin(tenant) as uow:
        return await uow.sessions.create(
            f.new_session(tenant_id=tenant, owner_id=owner_id, title=title)
        )


# --------------------------------------------------------------------------- sessions


@check
async def check_session_create_get_save(make: StoreFactory) -> None:
    store = await make()
    created = await _new_session(store, title="first")
    assert created.version == 0 and created.updated_at is not None
    async with store.begin(A) as uow:
        got = await uow.sessions.get(created.id)
        assert got is not None and got.title == "first" and got.version == 0
        assert await uow.sessions.get("ses_DOESNOTEXIST") is None
        locked = await uow.sessions.get(created.id, for_update=True)
        assert locked is not None and locked.id == created.id
        saved = await uow.sessions.save(
            got.model_copy(update={"state": "BRIEF_REVIEW", "title": "second"}),
            expected_version=0,
        )
        assert saved.version == 1 and saved.state == "BRIEF_REVIEW" and saved.title == "second"
        assert saved.updated_at is not None and saved.updated_at >= created.updated_at
        again = await uow.sessions.get(created.id)
        assert again is not None and again.version == 1 and again.state == "BRIEF_REVIEW"
        await _raises(Conflict, uow.sessions.create(created))  # same id twice


@check
async def check_session_save_is_compare_and_set(make: StoreFactory) -> None:
    store = await make()
    created = await _new_session(store)
    async with store.begin(A) as uow:
        v1 = await uow.sessions.save(created, expected_version=0)
        assert v1.version == 1
    async with store.begin(A) as uow:
        exc = await _raises(
            Conflict,
            uow.sessions.save(created.model_copy(update={"title": "late"}), expected_version=0),
        )
        assert exc.code == "conflict"
        current = await uow.sessions.get(created.id)
        assert current is not None and current.version == 1 and current.title is None
        v2 = await uow.sessions.save(v1.model_copy(update={"title": "ok"}), expected_version=1)
        assert v2.version == 2 and v2.title == "ok"
        ghost = f.new_session()
        await _raises(NotFound, uow.sessions.save(ghost, expected_version=0))


@check
async def check_session_concurrent_saves_one_winner(make: StoreFactory) -> None:
    store = await make()
    created = await _new_session(store)

    async def writer(n: int) -> bool:
        async with store.begin(A) as uow:
            try:
                await uow.sessions.save(
                    created.model_copy(update={"title": f"writer-{n}"}), expected_version=0
                )
            except Conflict:
                return False
            return True

    results = await asyncio.gather(*(writer(i) for i in range(6)))
    assert results.count(True) == 1, results
    async with store.begin(A) as uow:
        final = await uow.sessions.get(created.id)
        assert final is not None and final.version == 1


@check
async def check_session_list_for_owner(make: StoreFactory) -> None:
    store = await make()
    mine = [await _new_session(store, owner_id="usr_1") for _ in range(3)]
    await _new_session(store, owner_id="usr_2")
    async with store.begin(A) as uow:
        listed = await uow.sessions.list_for_owner("usr_1")
        assert {s.id for s in listed} == {s.id for s in mine}
        assert len(await uow.sessions.list_for_owner("usr_1", limit=2)) == 2
        assert await uow.sessions.list_for_owner("usr_nobody") == []


# --------------------------------------------------------------------------- events


@check
async def check_inbound_events_are_idempotent(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    event = f.user_message("hello")
    async with store.begin(A) as uow:
        assert await uow.events.record_inbound(ses.id, event) is True
    async with store.begin(A) as uow:
        assert await uow.events.record_inbound(ses.id, event) is False
        other = f.user_message("another")
        assert await uow.events.record_inbound(ses.id, other) is True
        await _raises(NotFound, uow.events.record_inbound("ses_DOESNOTEXIST", f.user_message()))


@check
async def check_concurrent_duplicate_inbound_stores_once(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    event = f.user_message("same event, many deliveries")

    async def deliver() -> bool:
        async with store.begin(A) as uow:
            return await uow.events.record_inbound(ses.id, event)

    results = await asyncio.gather(*(deliver() for _ in range(5)))
    assert results.count(True) == 1 and results.count(False) == 4, results


@check
async def check_outbound_seq_replay_and_replies(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    inbound = f.user_message("hi")
    async with store.begin(A) as uow:
        await uow.events.record_inbound(ses.id, inbound)
        first = await uow.events.append_outbound(
            ses.id, f.assistant_message(ses.id, text="one", in_reply_to=inbound.event_id)
        )
        second = await uow.events.append_outbound(
            ses.id, f.card_event(ses.id, in_reply_to=inbound.event_id)
        )
        third = await uow.events.append_outbound(ses.id, f.assistant_message(ses.id, text="three"))
        assert 0 < first.seq < second.seq < third.seq
        assert first.at is not None and first.in_reply_to == inbound.event_id
    async with store.begin(A) as uow:
        listed = await uow.events.list_outbound(ses.id)
        assert [e.seq for e in listed] == [first.seq, second.seq, third.seq]
        assert [e.event_id for e in listed] == [first.event_id, second.event_id, third.event_id]
        assert listed[0].type == "assistant_message" and listed[1].type == "card"
        after = await uow.events.list_outbound(ses.id, after_seq=first.seq)
        assert [e.seq for e in after] == [second.seq, third.seq]
        assert [e.seq for e in await uow.events.list_outbound(ses.id, limit=2)] == [
            first.seq,
            second.seq,
        ]
        replies = await uow.events.replies_to(ses.id, inbound.event_id)
        assert [e.event_id for e in replies] == [first.event_id, second.event_id]
        assert await uow.events.replies_to(ses.id, "evt_NOSUCHEVENT") == []
        await _raises(
            NotFound,
            uow.events.append_outbound("ses_DOESNOTEXIST", f.assistant_message("ses_DOESNOTEXIST")),
        )


@check
async def check_outbound_seq_is_unique_under_concurrency(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)

    async def append(n: int) -> int:
        async with store.begin(A) as uow:
            stored = await uow.events.append_outbound(
                ses.id, f.assistant_message(ses.id, text=f"m{n}")
            )
            return stored.seq

    seqs = await asyncio.gather(*(append(i) for i in range(12)))
    assert len(set(seqs)) == 12, "duplicate seq assigned"
    async with store.begin(A) as uow:
        listed = await uow.events.list_outbound(ses.id)
        assert [e.seq for e in listed] == sorted(seqs)


# --------------------------------------------------------------------------- brief / research


@check
async def check_brief_versions(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.briefs.latest(ses.id) is None
        v1 = await uow.briefs.add(f.new_brief(ses.id, A, version=1))
        v2 = await uow.briefs.add(f.new_brief(ses.id, A, version=2, summary="Changed."))
        assert v1.brief.version == 1 and v2.brief.version == 2
        got = await uow.briefs.get(ses.id, 1)
        assert got is not None and got.brief.hash == v1.brief.hash and "idea.summary" in got.slots
        latest = await uow.briefs.latest(ses.id)
        assert latest is not None and latest.brief.version == 2
        assert await uow.briefs.get(ses.id, 9) is None
        await _raises(Conflict, uow.briefs.add(f.new_brief(ses.id, A, version=2)))
        await uow.briefs.set_status(ses.id, 1, "superseded")
        await uow.briefs.set_status(ses.id, 2, "approved")
        got1, got2 = await uow.briefs.get(ses.id, 1), await uow.briefs.get(ses.id, 2)
        assert got1 is not None and got1.brief.status == "superseded"
        assert got2 is not None and got2.brief.status == "approved"
        await _raises(NotFound, uow.briefs.set_status(ses.id, 9, "approved"))


@check
async def check_research_notes(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.research.latest(ses.id) is None
        await uow.research.add(f.new_research(ses.id, A, version=1))
        second = await uow.research.add(f.new_research(ses.id, A, version=2, brief_hash="other"))
        latest = await uow.research.latest(ses.id)
        assert latest is not None and latest.note.version == 2
        assert latest.note.hash == second.note.hash and len(latest.note.facts) == 2
        await _raises(Conflict, uow.research.add(f.new_research(ses.id, A, version=2)))


# --------------------------------------------------------------------------- specs and approvals


@check
async def check_spec_versions_and_immutability(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.specs.latest(ses.id) is None
        draft = await uow.specs.add(f.new_spec(ses.id, A, version=1))
        assert draft.status == "draft" and draft.approved_at is None
        await _raises(Conflict, uow.specs.add(f.new_spec(ses.id, A, version=1)))  # no overwrite
        approved = await uow.specs.set_status(ses.id, 1, "approved")
        assert approved.status == "approved" and approved.approved_at is not None
        assert approved.spec_text == draft.spec_text and approved.hash == draft.hash
        # approved content can never be replaced, only moved forward
        await _raises(Conflict, uow.specs.add(f.new_spec(ses.id, A, version=1)))
        await _raises(MdeError, uow.specs.set_status(ses.id, 1, "draft"))
        handed = await uow.specs.set_status(ses.id, 1, "handed_off", git_sha="abc123")
        assert handed.status == "handed_off" and handed.git_sha == "abc123"
        assert handed.approved_at == approved.approved_at
        await _raises(MdeError, uow.specs.set_status(ses.id, 1, "approved"))
        await _raises(MdeError, uow.specs.set_status(ses.id, 1, "superseded"))
        await _raises(MdeError, uow.specs.set_status(ses.id, 1, "handed_off", git_sha="other"))
        await uow.specs.add(f.new_spec(ses.id, A, version=2))
        await uow.specs.set_status(ses.id, 2, "superseded")
        await _raises(MdeError, uow.specs.set_status(ses.id, 2, "approved"))
        listed = await uow.specs.list_versions(ses.id)
        assert [s.version for s in listed] == [1, 2]
        assert [s.status for s in listed] == ["handed_off", "superseded"]
        latest = await uow.specs.latest(ses.id)
        assert latest is not None and latest.version == 2
        got = await uow.specs.get(ses.id, 1)
        assert got is not None and got.spec_text == draft.spec_text and got.status == "handed_off"
        assert await uow.specs.get(ses.id, 9) is None
        await _raises(NotFound, uow.specs.set_status(ses.id, 9, "approved"))
        illegal = await uow.specs.add(f.new_spec(ses.id, A, version=3))
        assert illegal.status == "draft"
        await _raises(MdeError, uow.specs.set_status(ses.id, 3, "handed_off"))


@check
async def check_approvals(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        a1 = await uow.approvals.add(f.new_approval(ses.id, gate="brief", version=1, hash="h1"))
        a2 = await uow.approvals.add(
            f.new_approval(ses.id, gate="brief", version=1, hash="h1", approver_id="usr_2")
        )
        await uow.approvals.add(f.new_approval(ses.id, gate="brief", version=2, hash="h2"))
        await uow.approvals.add(f.new_approval(ses.id, gate="plan", version=1, hash="p1"))
        assert a1.at is not None
        both = await uow.approvals.list(ses.id, "brief", version=1, hash="h1")
        assert [a.id for a in both] == [a1.id, a2.id]
        assert len(await uow.approvals.list(ses.id, "brief")) == 3
        assert len(await uow.approvals.list(ses.id, "brief", version=2)) == 1
        assert await uow.approvals.list(ses.id, "brief", hash="nope") == []
        assert len(await uow.approvals.list(ses.id, "plan")) == 1
        await _raises(Conflict, uow.approvals.add(a1))


# --------------------------------------------------------------------------- job queue


@check
async def check_one_active_run_per_session(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        job = await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())
        assert job.state == "queued" and job.attempts == 0 and job.session_id == ses.id
        active = await uow.jobs.active_for_session(ses.id)
        assert active is not None and active.id == job.id
        await _raises(Conflict, uow.jobs.enqueue_plan(ses.id, A, f.plan_payload(spec_version=2)))
    async with store.begin(None) as uow:
        claimed = await uow.jobs.claim("worker-1")
        assert claimed is not None and claimed.id == job.id
    async with store.begin(A) as uow:  # a running job is still the active run
        await _raises(Conflict, uow.jobs.enqueue_plan(ses.id, A, f.plan_payload(spec_version=2)))
        await uow.jobs.complete(job.id)
        assert await uow.jobs.active_for_session(ses.id) is None
        done = await uow.jobs.get(job.id)
        assert done is not None and done.state == "succeeded"
        again = await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload(spec_version=2))
        assert again.id != job.id and again.state == "queued"


@check
async def check_concurrent_enqueue_allows_one(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)

    async def enqueue() -> bool:
        async with store.begin(A) as uow:
            try:
                await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())
            except Conflict:
                return False
            return True

    results = await asyncio.gather(*(enqueue() for _ in range(6)))
    assert results.count(True) == 1, results


@check
async def check_claim_is_exclusive(make: StoreFactory) -> None:
    store = await make()
    sessions = [await _new_session(store) for _ in range(5)]
    queued: set[str] = set()
    for ses in sessions:
        async with store.begin(A) as uow:
            queued.add((await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())).id)

    async def claim(n: int) -> str | None:
        async with store.begin(None) as uow:
            job = await uow.jobs.claim(f"worker-{n}")
            return None if job is None else job.id

    claims = await asyncio.gather(*(claim(i) for i in range(10)))
    got = [c for c in claims if c is not None]
    assert len(got) == 5 and set(got) == queued, claims  # each job exactly once
    async with store.begin(None) as uow:
        assert await uow.jobs.claim("worker-late") is None


@check
async def check_claim_marks_running_and_is_fifo(make: StoreFactory) -> None:
    store = await make()
    s1, s2 = await _new_session(store), await _new_session(store)
    async with store.begin(A) as uow:
        first = await uow.jobs.enqueue_plan(s1.id, A, f.plan_payload())
    async with store.begin(A) as uow:
        second = await uow.jobs.enqueue_plan(s2.id, A, f.plan_payload())
    async with store.begin(None) as uow:
        assert await uow.jobs.claim("w") is not None  # takes the oldest
    async with store.begin(A) as uow:
        one, two = await uow.jobs.get(first.id), await uow.jobs.get(second.id)
        assert one is not None and two is not None
        assert one.state == "running" and one.lock_owner == "w" and one.attempts == 1
        assert one.heartbeat_at is not None
        assert two.state == "queued" and two.lock_owner is None and two.attempts == 0
        await uow.jobs.heartbeat(first.id, "w")
        await uow.jobs.set_phase(first.id, "build")
        refreshed = await uow.jobs.get(first.id)
        assert refreshed is not None and refreshed.phase == "build"
        assert refreshed.heartbeat_at is not None and one.heartbeat_at is not None
        assert refreshed.heartbeat_at >= one.heartbeat_at


@check
async def check_cancel_queued_and_running(make: StoreFactory) -> None:
    store = await make()
    s1, s2 = await _new_session(store), await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.jobs.cancel(s1.id) is False  # nothing to cancel
        queued = await uow.jobs.enqueue_plan(s1.id, A, f.plan_payload())
        assert await uow.jobs.cancel(s1.id) is True
        gone = await uow.jobs.get(queued.id)
        assert gone is not None and gone.state == "cancelled"
        assert await uow.jobs.active_for_session(s1.id) is None
        await uow.jobs.enqueue_plan(s1.id, A, f.plan_payload())  # slot is free again
        running = await uow.jobs.enqueue_plan(s2.id, A, f.plan_payload(spec_version=5))
    async with store.begin(None) as uow:
        claimed_ids = set()
        for _ in range(2):
            job = await uow.jobs.claim("w")
            assert job is not None
            claimed_ids.add(job.id)
        assert running.id in claimed_ids
    async with store.begin(A) as uow:
        assert await uow.jobs.is_cancel_requested(running.id) is False
        assert await uow.jobs.cancel(s2.id) is True
        assert await uow.jobs.is_cancel_requested(running.id) is True
        still = await uow.jobs.get(running.id)
        assert still is not None and still.state == "running" and still.cancel_requested is True
        await _raises(NotFound, uow.jobs.is_cancel_requested("job_DOESNOTEXIST"))


@check
async def check_recover_stuck_requeues_then_fails(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        job = await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())
    async with store.begin(None) as uow:
        assert await uow.jobs.recover_stuck(older_than_seconds=3600) == 0  # nothing queued->running
        assert await uow.jobs.claim("w1") is not None
        assert await uow.jobs.recover_stuck(older_than_seconds=3600) == 0  # heartbeat is fresh
    for attempt in (2, 3):  # stale heartbeat (negative age: everything running is stale)
        async with store.begin(None) as uow:
            assert await uow.jobs.recover_stuck(older_than_seconds=-1) == 1
        async with store.begin(A) as uow:
            back = await uow.jobs.get(job.id)
            assert back is not None and back.state == "queued" and back.lock_owner is None
        async with store.begin(None) as uow:
            claimed = await uow.jobs.claim(f"w{attempt}")
            assert claimed is not None and claimed.attempts == attempt
    async with store.begin(None) as uow:  # third attempt used up: next recovery fails the job
        assert await uow.jobs.recover_stuck(older_than_seconds=-1) == 1
        assert await uow.jobs.claim("w4") is None
    async with store.begin(A) as uow:
        failed = await uow.jobs.get(job.id)
        assert failed is not None and failed.state == "failed" and failed.attempts == 3
        assert await uow.jobs.active_for_session(ses.id) is None


@check
async def check_job_complete_outcomes(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(A) as uow:
        job = await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())
    async with store.begin(None) as uow:
        assert await uow.jobs.claim("w") is not None
    async with store.begin(A) as uow:
        await uow.jobs.complete(job.id, error="boom")  # attempts left: another try
        retry = await uow.jobs.get(job.id)
        assert retry is not None and retry.state == "queued" and retry.error == "boom"
    for _ in range(2):
        async with store.begin(None) as uow:
            assert await uow.jobs.claim("w") is not None
        async with store.begin(A) as uow:
            await uow.jobs.complete(job.id, error="boom again")
    async with store.begin(A) as uow:
        final = await uow.jobs.get(job.id)
        assert final is not None and final.state == "failed" and final.attempts == 3
        assert final.error == "boom again"
        assert await uow.jobs.active_for_session(ses.id) is None


# --------------------------------------------------------------------------- tenancy


@check
async def check_tenant_isolation_everywhere(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store, A, owner_id="usr_1")
    inbound = f.user_message("secret")
    async with store.begin(A) as uow:
        await uow.events.record_inbound(ses.id, inbound)
        out = await uow.events.append_outbound(ses.id, f.assistant_message(ses.id))
        await uow.briefs.add(f.new_brief(ses.id, A))
        await uow.research.add(f.new_research(ses.id, A))
        await uow.specs.add(f.new_spec(ses.id, A))
        approval = await uow.approvals.add(f.new_approval(ses.id))
        job = await uow.jobs.enqueue_plan(ses.id, A, f.plan_payload())
    foreign = f.new_session(tenant_id=B, owner_id="usr_1")
    async with store.begin(B) as uow:
        # reads behave as "not found" / empty
        assert await uow.sessions.get(ses.id) is None
        assert await uow.sessions.get(ses.id, for_update=True) is None
        assert await uow.sessions.list_for_owner("usr_1") == []
        await _empty_or_not_found(uow.events.list_outbound(ses.id))
        await _empty_or_not_found(uow.events.replies_to(ses.id, inbound.event_id))
        assert await uow.briefs.get(ses.id, 1) is None and await uow.briefs.latest(ses.id) is None
        assert await uow.research.latest(ses.id) is None
        assert await uow.specs.get(ses.id, 1) is None and await uow.specs.latest(ses.id) is None
        await _empty_or_not_found(uow.specs.list_versions(ses.id))
        await _empty_or_not_found(uow.approvals.list(ses.id, "brief"))
        assert await uow.jobs.get(job.id) is None
        assert await uow.jobs.active_for_session(ses.id) is None
        # writes are refused
        both = (TenantViolation, NotFound)
        await _raises(both, uow.sessions.save(ses, expected_version=0))
        await _raises(
            both, uow.sessions.save(ses.model_copy(update={"tenant_id": B}), expected_version=0)
        )
        await _raises(both, uow.sessions.create(ses.model_copy(update={"id": "ses_OTHER"})))
        await _raises(NotFound, uow.events.record_inbound(ses.id, f.user_message()))
        await _raises(NotFound, uow.events.append_outbound(ses.id, f.assistant_message(ses.id)))
        await _raises(both, uow.briefs.add(f.new_brief(ses.id, A, version=5)))
        await _raises(both, uow.briefs.add(f.new_brief(ses.id, B, version=5)))
        await _raises(NotFound, uow.briefs.set_status(ses.id, 1, "approved"))
        await _raises(both, uow.research.add(f.new_research(ses.id, B, version=5)))
        await _raises(both, uow.specs.add(f.new_spec(ses.id, B, version=5)))
        await _raises(NotFound, uow.specs.set_status(ses.id, 1, "approved"))
        await _raises(both, uow.approvals.add(f.new_approval(ses.id)))
        await _raises(both, uow.jobs.enqueue_plan(ses.id, B, f.plan_payload(spec_version=7)))
        await _raises(both, uow.jobs.enqueue_plan(ses.id, A, f.plan_payload(spec_version=7)))
        assert await uow.jobs.cancel(ses.id) is False
        await _raises(NotFound, uow.jobs.is_cancel_requested(job.id))
        await _raises(NotFound, uow.jobs.heartbeat(job.id, "w"))
        await _raises(NotFound, uow.jobs.set_phase(job.id, "build"))
        await _raises(NotFound, uow.jobs.complete(job.id))
        # a tenant only ever sees its own rows, even under the same ids
        await uow.sessions.create(foreign)
        assert [s.id for s in await uow.sessions.list_for_owner("usr_1")] == [foreign.id]
    # tenant A's data is untouched
    async with store.begin(A) as uow:
        mine = await uow.sessions.get(ses.id)
        assert mine is not None and mine.version == 0 and mine.tenant_id == A
        assert [e.event_id for e in await uow.events.list_outbound(ses.id)] == [out.event_id]
        assert (await uow.briefs.latest(ses.id)) is not None
        assert len(await uow.approvals.list(ses.id, "brief")) == 1 and approval.id
        active = await uow.jobs.active_for_session(ses.id)
        assert active is not None and active.id == job.id and not active.cancel_requested
        spec = await uow.specs.get(ses.id, 1)
        assert spec is not None and spec.status == "draft"


@check
async def check_system_scope_is_limited_to_the_queue(make: StoreFactory) -> None:
    store = await make()
    ses = await _new_session(store)
    async with store.begin(None) as uow:
        await _raises(TenantViolation, uow.sessions.get(ses.id))
        await _raises(TenantViolation, uow.sessions.list_for_owner("usr_owner"))
        await _raises(TenantViolation, uow.events.list_outbound(ses.id))
        await _raises(TenantViolation, uow.briefs.latest(ses.id))
        await _raises(TenantViolation, uow.research.latest(ses.id))
        await _raises(TenantViolation, uow.specs.latest(ses.id))
        await _raises(TenantViolation, uow.approvals.list(ses.id, "brief"))
        await _raises(TenantViolation, uow.jobs.active_for_session(ses.id))
        await _raises(TenantViolation, uow.jobs.enqueue_plan(ses.id, A, f.plan_payload()))
        assert await uow.jobs.claim("w") is None  # claim and recovery are what system scope is for
        assert await uow.jobs.recover_stuck(older_than_seconds=60) == 0
    async with store.begin(A) as uow:  # tenant scope cannot claim or recover
        await _raises(TenantViolation, uow.jobs.claim("w"))
        await _raises(TenantViolation, uow.jobs.recover_stuck(older_than_seconds=60))


# --------------------------------------------------------------------------- transactions


@check
async def check_commit_and_rollback(make: StoreFactory) -> None:
    store = await make()
    kept = f.new_session()
    async with store.begin(A) as uow:
        await uow.sessions.create(kept)
    lost = f.new_session()
    try:
        async with store.begin(A) as uow:
            await uow.sessions.create(lost)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    flushed = f.new_session()
    try:
        async with store.begin(A) as uow:
            await uow.sessions.create(flushed)
            await uow.commit()
            await uow.sessions.save(
                (await uow.sessions.get(flushed.id)) or flushed, expected_version=0
            )
            raise RuntimeError("boom after commit")
    except RuntimeError:
        pass
    async with store.begin(A) as uow:
        assert await uow.sessions.get(kept.id) is not None
        assert await uow.sessions.get(lost.id) is None, "rolled back on exception"
        survivor = await uow.sessions.get(flushed.id)
        assert survivor is not None and survivor.version == 0, "work after commit rolled back"
    try:
        async with store.begin(A) as uow:
            await uow.sessions.create(f.new_session())
            await uow.rollback()
            raise RuntimeError("after explicit rollback")
    except RuntimeError:
        pass
    async with store.begin(A) as uow:
        assert len(await uow.sessions.list_for_owner("usr_owner")) == 2
