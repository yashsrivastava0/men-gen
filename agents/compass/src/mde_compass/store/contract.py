"""The ONE shared store contract suite (repo ADR 0007): the semantics every ``Store`` must have.

``PostgresStore`` and ``InMemoryStore`` run exactly these checks (``tests/store``), so a flow or engine
test built on the fake says something about the real store. Every check is written only against the
ports in ``mde_compass.store.ports``.

Usage (pytest)::

    @pytest.mark.parametrize("name", sorted(CHECKS))
    async def test_contract(name, factory):
        await CHECKS[name](factory)   # factory: async callable returning an EMPTY Store

A few checks need to move time (stale turns): they read ``store.clock`` and require a ``FakeClock``,
which both test fixtures inject.
"""

# ruff: noqa: PT018  (compound asserts keep the contract checks readable)
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, TypeVar, cast

from pydantic import JsonValue

from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.domain.interview import FactEffect
from mde_compass.domain.session import SessionRecord
from mde_compass.store import testing as f
from mde_compass.store.ports import CallLogEntry, Store
from mde_core.clock import FakeClock
from mde_core.errors import Conflict, MdeError, NotFound, TenantViolation

StoreFactory = Callable[[], Awaitable[Store]]
Check = Callable[[StoreFactory], Awaitable[None]]

T = TypeVar("T")
CHECKS: dict[str, Check] = {}

A = f.TENANT_A
B = f.TENANT_B
CONCURRENCY = 6  # below the Postgres pool size (5 + waiting): units of work finish one by one


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


async def _new_session(
    store: Store, tenant: str = A, *, user_id: str = f.USER_A, **fields: Any
) -> SessionRecord:
    async with store.begin(tenant) as uow:
        return await uow.sessions.create(
            f.make_session(tenant_id=tenant, user_id=user_id, **fields)
        )


def _payload(text: str, **more: JsonValue) -> dict[str, JsonValue]:
    return {"text": text, **more}


# --------------------------------------------------------------------------- sessions


@check
async def check_session_create_get_list_save(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store, title="First")
    assert rec.tenant_id == A and rec.row_version == 0 and rec.state == "WELCOME"
    assert rec.created_at is not None and rec.updated_at is not None
    assert rec.outbound_seq == 0 and rec.active_turn is False and rec.spent_micros == 0

    async with store.begin(A) as uow:
        got = await uow.sessions.get(rec.id)
        assert got.id == rec.id and got.title == "First" and got.user_id == f.USER_A
        assert got.created_at is not None and got.created_at.tzinfo is not None
        listed = await uow.sessions.list_for_user(f.USER_A)
        assert [s.id for s in listed] == [rec.id]

    async with store.begin(A) as uow:
        got.title = "Renamed"
        got.state = "INTERVIEWING"
        got.brief_version = 3
        saved = await uow.sessions.save(got)
        assert saved.row_version == 1 and saved.title == "Renamed" and saved.state == "INTERVIEWING"
    async with store.begin(A) as uow:
        again = await uow.sessions.get(rec.id)
        assert again.row_version == 1 and again.brief_version == 3 and again.title == "Renamed"
        await _raises(NotFound, uow.sessions.get("ses_does_not_exist"))


@check
async def check_session_save_roundtrips_mutable_fields(factory: StoreFactory) -> None:
    store = await factory()
    clock = cast("FakeClock", getattr(store, "clock"))  # noqa: B009
    rec = await _new_session(store, title="t0", locale="en")
    clock.advance(30)
    async with store.begin(A) as uow:
        cur = await uow.sessions.get(rec.id)
        cur.mode = "enterprise"
        cur.locale = "hi"
        cur.user_label = "Asha"
        cur.title = "Pune thali app"
        cur.state = "PAUSED"
        cur.resume_state = "INTERVIEWING"
        cur.brief_version = 4
        cur.approved_version = 3
        cur.approved_hash = "h" * 64
        cur.contributors = [
            f.make_contributor("con_1"),
            f.make_contributor("con_2", role="approver"),
        ]
        cur.user_id = "usr_someone_else"  # the owner never changes
        saved = await uow.sessions.save(cur)
        assert saved.user_id == f.USER_A and saved.tenant_id == A
    async with store.begin(A) as uow:
        got = await uow.sessions.get(rec.id)
        assert (got.mode, got.locale, got.user_label, got.title) == (
            "enterprise",
            "hi",
            "Asha",
            "Pune thali app",
        )
        assert (got.state, got.resume_state) == ("PAUSED", "INTERVIEWING")
        assert (got.brief_version, got.approved_version, got.approved_hash) == (4, 3, "h" * 64)
        assert [(c.id, c.role) for c in got.contributors] == [
            ("con_1", "requester"),
            ("con_2", "approver"),
        ]
        assert got.user_id == f.USER_A and got.created_at == rec.created_at
        assert (
            got.updated_at is not None
            and rec.updated_at is not None
            and got.updated_at > rec.updated_at
        )
        assert [s.id for s in await uow.sessions.list_for_user(f.USER_A)] == [rec.id]
        assert await uow.sessions.list_for_user("usr_someone_else") == []


@check
async def check_session_create_duplicate_and_tenant(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await _raises(Conflict, uow.sessions.create(rec))
    async with store.begin(A) as uow:
        await _raises(TenantViolation, uow.sessions.create(f.make_session(tenant_id=B)))
    async with store.begin(A) as uow:  # nothing of the failed attempts leaked
        assert len(await uow.sessions.list_for_user(f.USER_A)) == 1
        assert await uow.sessions.list_for_user(f.USER_B) == []


@check
async def check_session_save_stale_conflict(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        first = await uow.sessions.get(rec.id)
        second = await uow.sessions.get(rec.id)
        first.title = "winner"
        await uow.sessions.save(first)
        second.title = "loser"
        err = await _raises(Conflict, uow.sessions.save(second))
        assert "stale" in str(err).lower() or "row_version" in str(err)
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(rec.id)).title == "winner"
        # saving an unknown session is NotFound, not Conflict
        ghost = f.make_session(tenant_id=A)
        await _raises(NotFound, uow.sessions.save(ghost))
        # a record bound to another tenant is refused outright
        await _raises(TenantViolation, uow.sessions.save(rec.model_copy(update={"tenant_id": B})))


@check
async def check_session_concurrent_save_one_winner(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def attempt(n: int) -> str:
        try:
            async with store.begin(A) as uow:
                cur = await uow.sessions.get(rec.id)
                cur.title = f"writer {n}"
                await uow.sessions.save(cur)
            return "ok"
        except Conflict:
            return "conflict"

    # all read row_version 0 at about the same time; the optimistic check lets only one win
    results = await asyncio.gather(*(attempt(i) for i in range(3)))
    assert results.count("ok") >= 1
    async with store.begin(A) as uow:
        final = await uow.sessions.get(rec.id)
        assert final.row_version == results.count("ok")


@check
async def check_session_save_keeps_counters(factory: StoreFactory) -> None:
    """A stale record saved later must not reset spend, the sequence counter or the turn flag."""
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        stale = await uow.sessions.get(rec.id)
    async with store.begin(A) as uow:
        await uow.sessions.add_spend(rec.id, 700, calls=2)
        await uow.events.append_outbound(rec.id, "assistant_message", _payload("hi"))
        assert await uow.sessions.try_begin_turn(rec.id)
    async with store.begin(A) as uow:
        stale.title = "edited"
        await uow.sessions.save(stale)
    async with store.begin(A) as uow:
        now = await uow.sessions.get(rec.id)
        assert now.title == "edited"
        assert now.spent_micros == 700 and now.llm_calls == 2
        assert now.outbound_seq == 1 and now.active_turn is True


@check
async def check_session_list_for_user(factory: StoreFactory) -> None:
    store = await factory()
    clock = cast("FakeClock", getattr(store, "clock"))  # noqa: B009
    ids: list[str] = []
    for i in range(3):
        rec = await _new_session(store, title=f"s{i}")
        ids.append(rec.id)
        clock.advance(10)
    await _new_session(store, user_id=f.USER_B)
    async with store.begin(A) as uow:
        listed = await uow.sessions.list_for_user(f.USER_A)
        assert [s.id for s in listed] == list(reversed(ids))  # most recently updated first
        assert len(await uow.sessions.list_for_user(f.USER_A, limit=2)) == 2
        old = await uow.sessions.set_state(ids[0], "ARCHIVED")
        assert old.state == "ARCHIVED"
        visible = await uow.sessions.list_for_user(f.USER_A)
        assert ids[0] not in [s.id for s in visible]
        everything = await uow.sessions.list_for_user(f.USER_A, include_archived=True)
        assert ids[0] in [s.id for s in everything] and len(everything) == 3
        assert [s.user_id for s in await uow.sessions.list_for_user(f.USER_B)] == [f.USER_B]


@check
async def check_turn_exclusive(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.sessions.try_begin_turn(rec.id) is True
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(rec.id)).active_turn is True
        assert await uow.sessions.try_begin_turn(rec.id) is False
    async with store.begin(A) as uow:
        await uow.sessions.end_turn(rec.id)
        await uow.sessions.end_turn(rec.id)  # ending twice is harmless
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(rec.id)).active_turn is False
        assert await uow.sessions.try_begin_turn(rec.id) is True
    async with store.begin(A) as uow:
        await _raises(NotFound, uow.sessions.try_begin_turn("ses_missing"))
        await _raises(NotFound, uow.sessions.end_turn("ses_missing"))


@check
async def check_turn_race_single_winner(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def claim() -> bool:
        async with store.begin(A) as uow:
            return await uow.sessions.try_begin_turn(rec.id)

    results = await asyncio.gather(*(claim() for _ in range(CONCURRENCY)))
    assert results.count(True) == 1 and results.count(False) == CONCURRENCY - 1

    async with store.begin(A) as uow:
        await uow.sessions.end_turn(rec.id)
    again = await asyncio.gather(*(claim() for _ in range(CONCURRENCY)))
    assert again.count(True) == 1


@check
async def check_turn_stale_takeover(factory: StoreFactory) -> None:
    store = await factory()
    clock = cast("FakeClock", getattr(store, "clock"))  # noqa: B009
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.sessions.try_begin_turn(rec.id) is True
    clock.advance(60)
    async with store.begin(A) as uow:
        assert await uow.sessions.try_begin_turn(rec.id) is False  # still fresh
    clock.advance(61)  # 121 s since the turn began
    async with store.begin(A) as uow:
        assert await uow.sessions.try_begin_turn(rec.id) is True  # taken over
    async with store.begin(A) as uow:
        assert await uow.sessions.try_begin_turn(rec.id) is False  # and fresh again


@check
async def check_spend_and_state(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.sessions.add_spend(rec.id, 1500) == 1500
        assert await uow.sessions.add_spend(rec.id, 2500, calls=3) == 4000
        await _raises(MdeError, uow.sessions.add_spend(rec.id, -1))
        await _raises(NotFound, uow.sessions.add_spend("ses_missing", 1))
    async with store.begin(A) as uow:
        got = await uow.sessions.get(rec.id)
        assert got.spent_micros == 4000 and got.llm_calls == 4
        assert got.row_version == 0  # counters are not part of the optimistic token
        moved = await uow.sessions.set_state(rec.id, "INTERVIEWING")
        assert moved.state == "INTERVIEWING" and moved.row_version == 1
        await _raises(NotFound, uow.sessions.set_state("ses_missing", "PAUSED"))
    async with store.begin(A) as uow:
        # a record read before set_state is now stale
        await _raises(Conflict, uow.sessions.save(got))
        assert (await uow.sessions.get(rec.id)).state == "INTERVIEWING"


@check
async def check_spend_concurrent(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def spend() -> None:
        async with store.begin(A) as uow:
            await uow.sessions.add_spend(rec.id, 100)

    await asyncio.gather(*(spend() for _ in range(CONCURRENCY)))
    async with store.begin(A) as uow:
        got = await uow.sessions.get(rec.id)
        assert got.spent_micros == 100 * CONCURRENCY and got.llm_calls == CONCURRENCY


# --------------------------------------------------------------------------- events


@check
async def check_events_inbound_idempotent(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert (
            await uow.events.record_inbound(rec.id, "e1", "user_message", _payload("hello")) is True
        )
        assert (
            await uow.events.record_inbound(rec.id, "e1", "user_message", _payload("again"))
            is False
        )
        assert await uow.events.record_inbound(rec.id, "e2", "card_skip", {"card_id": "Q-001"})
    async with store.begin(A) as uow:
        assert await uow.events.record_inbound(rec.id, "e1", "user_message", _payload("x")) is False
        first = await uow.events.get_inbound(rec.id, "e1")
        assert first is not None and first.payload == _payload(
            "hello"
        )  # the duplicate wrote nothing
        assert first.direction == "in" and first.type == "user_message" and first.seq is None
        assert first.event_id == "e1" and first.at is not None
        assert await uow.events.get_inbound(rec.id, "nope") is None
        page = await uow.events.list_history(rec.id)
        assert [e.event_id for e in page.items] == ["e1", "e2"]
        assert [e.idx for e in page.items] == sorted(e.idx for e in page.items)
        assert len({e.idx for e in page.items}) == 2
        await _raises(NotFound, uow.events.get_inbound("ses_missing", "e1"))
        await _raises(NotFound, uow.events.record_inbound("ses_missing", "e1", "t", {}))


@check
async def check_events_inbound_idempotent_concurrent(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def record() -> bool:
        async with store.begin(A) as uow:
            return await uow.events.record_inbound(rec.id, "same", "user_message", _payload("hi"))

    results = await asyncio.gather(*(record() for _ in range(CONCURRENCY)))
    assert results.count(True) == 1
    async with store.begin(A) as uow:
        assert len((await uow.events.list_history(rec.id)).items) == 1


@check
async def check_events_outbound_seq(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    other = await _new_session(store)
    async with store.begin(A) as uow:
        seqs = [
            await uow.events.append_outbound(
                rec.id, "assistant_message", _payload(f"m{i}"), turn_id="t1"
            )
            for i in range(5)
        ]
        assert seqs == [1, 2, 3, 4, 5]
        assert await uow.events.append_outbound(other.id, "notice", {"code": "x"}) == 1
    async with store.begin(A) as uow:
        assert await uow.events.append_outbound(rec.id, "progress", {"stage": "s"}) == 6
        everything = await uow.events.list_outbound(rec.id)
        assert [e.seq for e in everything] == [1, 2, 3, 4, 5, 6]
        assert everything[0].direction == "out" and everything[0].turn_id == "t1"
        assert everything[0].event_id is None and everything[0].at is not None
        assert everything[0].payload == _payload("m0")
        assert [e.seq for e in await uow.events.list_outbound(rec.id, after_seq=3)] == [4, 5, 6]
        assert [e.seq for e in await uow.events.list_outbound(rec.id, after_seq=1, limit=2)] == [
            2,
            3,
        ]
        assert await uow.events.list_outbound(rec.id, after_seq=6) == []
        assert (await uow.sessions.get(rec.id)).outbound_seq == 6
        await _raises(NotFound, uow.events.append_outbound("ses_missing", "notice", {}))


@check
async def check_events_outbound_concurrent(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def append(n: int) -> int:
        async with store.begin(A) as uow:
            return await uow.events.append_outbound(rec.id, "assistant_message", _payload(f"m{n}"))

    seqs = await asyncio.gather(*(append(i) for i in range(CONCURRENCY)))
    assert sorted(seqs) == list(range(1, CONCURRENCY + 1))
    async with store.begin(A) as uow:
        stored = await uow.events.list_outbound(rec.id)
        assert [e.seq for e in stored] == list(range(1, CONCURRENCY + 1))
        # the sequence number equals the order in which the writers committed
        assert [e.idx for e in stored] == sorted(e.idx for e in stored)


@check
async def check_events_outbound_batches_do_not_interleave(factory: StoreFactory) -> None:
    """A unit of work holds the session's sequence until it ends: its events get consecutive numbers."""
    store = await factory()
    rec = await _new_session(store)
    per_writer = 3

    async def writer(n: int) -> list[int]:
        async with store.begin(A) as uow:
            return [
                await uow.events.append_outbound(rec.id, "assistant_message", _payload(f"w{n}-{i}"))
                for i in range(per_writer)
            ]

    batches = await asyncio.gather(*(writer(n) for n in range(4)))
    flat = sorted(seq for batch in batches for seq in batch)
    assert flat == list(range(1, 4 * per_writer + 1))
    for batch in batches:
        assert batch == list(range(batch[0], batch[0] + per_writer))  # consecutive, in order
    async with store.begin(A) as uow:
        stored = await uow.events.list_outbound(rec.id)
        assert [e.seq for e in stored] == flat
        assert [e.idx for e in stored] == sorted(e.idx for e in stored)


@check
async def check_turn_and_event_writers_do_not_deadlock(factory: StoreFactory) -> None:
    """Writers that touch events and the turn flag in opposite orders still finish (one lock order)."""
    store = await factory()
    rec = await _new_session(store)

    async def events_then_turn() -> None:
        async with store.begin(A) as uow:
            await uow.events.append_outbound(rec.id, "notice", {"code": "a"})
            await uow.sessions.try_begin_turn(rec.id)

    async def turn_then_events() -> None:
        async with store.begin(A) as uow:
            await uow.sessions.try_begin_turn(rec.id)
            await uow.events.append_outbound(rec.id, "notice", {"code": "b"})

    jobs = [events_then_turn(), turn_then_events(), events_then_turn(), turn_then_events()]
    await asyncio.wait_for(asyncio.gather(*jobs), timeout=20)
    async with store.begin(A) as uow:
        assert [e.seq for e in await uow.events.list_outbound(rec.id)] == [1, 2, 3, 4]


@check
async def check_events_history_paging(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        for i in range(1, 4):
            await uow.events.record_inbound(
                rec.id, f"u{i}", "user_message", _payload(f"question {i}")
            )
            await uow.events.append_outbound(
                rec.id, "assistant_message", _payload(f"answer {i}"), turn_id=f"u{i}"
            )
    async with store.begin(A) as uow:
        page1 = await uow.events.list_history(rec.id, limit=4)
        assert len(page1.items) == 4 and page1.next_after_idx == page1.items[-1].idx
        assert [e.direction for e in page1.items] == ["in", "out", "in", "out"]
        page2 = await uow.events.list_history(rec.id, after_idx=page1.next_after_idx or 0, limit=4)
        assert len(page2.items) == 2 and page2.next_after_idx is None
        assert [e.type for e in page2.items] == ["user_message", "assistant_message"]
        assert page2.items[0].idx > page1.items[-1].idx
        exact = await uow.events.list_history(rec.id, limit=6)
        assert len(exact.items) == 6 and exact.next_after_idx is None
        assert (await uow.events.list_history(rec.id, after_idx=99)).items == []
        await _raises(NotFound, uow.events.list_history("ses_missing"))


@check
async def check_events_history_filters(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await uow.events.record_inbound(
            rec.id, "u1", "user_message", _payload("A food app for PUNE")
        )
        await uow.events.append_outbound(
            rec.id, "assistant_message", _payload("Which city?"), turn_id="u1"
        )
        await uow.events.record_inbound(rec.id, "u2", "user_message", _payload("मुझे हिंदी चाहिए"))
        await uow.events.append_outbound(
            rec.id, "card", {"card": {"question": "Pune or Mumbai?"}}, turn_id="u2"
        )
        await uow.events.record_inbound(
            rec.id, "u3", "card_answer", {"card_id": "Q-1", "value": "A"}
        )
    async with store.begin(A) as uow:
        hits = await uow.events.list_history(rec.id, q="pune")
        assert [e.type for e in hits.items] == [
            "user_message",
            "card",
        ]  # case-insensitive, nested text
        assert [
            e.event_id for e in (await uow.events.list_history(rec.id, q="PUNE FOOD")).items
        ] == []
        assert len((await uow.events.list_history(rec.id, q="food app")).items) == 1
        assert len((await uow.events.list_history(rec.id, q="हिंदी")).items) == 1
        # keys and non-string values are not searched
        assert (await uow.events.list_history(rec.id, q="card_id")).items == []
        assert (await uow.events.list_history(rec.id, q="no such text")).items == []
        by_turn = await uow.events.list_history(rec.id, turn_ids=["u2"])
        assert [(e.direction, e.type) for e in by_turn.items] == [
            ("in", "user_message"),
            ("out", "card"),
        ]
        both = await uow.events.list_history(rec.id, turn_ids=["u1", "u3"])
        assert [e.type for e in both.items] == ["user_message", "assistant_message", "card_answer"]
        assert (await uow.events.list_history(rec.id, turn_ids=[])).items == []
        mixed = await uow.events.list_history(rec.id, q="pune", turn_ids=["u2"])
        assert [e.type for e in mixed.items] == ["card"]
        # LIKE wildcards in the query are plain text, and a NUL in the query is just dropped
        assert (await uow.events.list_history(rec.id, q="%")).items == []
        assert len((await uow.events.list_history(rec.id, q="pu\x00ne")).items) == 2
        assert (await uow.events.list_history(rec.id, turn_ids=["u\x002"])).items != []


@check
async def check_events_payload_roundtrip(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    payload: dict[str, JsonValue] = {
        "text": "naïve café \x00 NUL",
        "n": 3,
        "nested": {"list": [1, 2.5, None, True, "x"], "empty": {}},
    }
    async with store.begin(A) as uow:
        await uow.events.record_inbound(rec.id, "e1", "user_message", payload)
        await uow.events.append_outbound(rec.id, "notice", payload)
    async with store.begin(A) as uow:
        got = await uow.events.get_inbound(rec.id, "e1")
        assert got is not None
        expected: dict[str, JsonValue] = {**payload, "text": "naïve café  NUL"}  # NUL removed
        assert got.payload == expected
        out = (await uow.events.list_outbound(rec.id))[0]
        assert out.payload == expected


# --------------------------------------------------------------------------- briefs


@check
async def check_briefs_save_get_list(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await _raises(NotFound, uow.briefs.get(rec.id))
        await _raises(NotFound, uow.briefs.get(rec.id, 1))
        assert await uow.briefs.list_versions(rec.id) == []
        v1 = await uow.briefs.save_version(rec.id, f.make_brief(1, summary="one"))
        assert v1.version == 1 and v1.session_id == rec.id and v1.baseline_label is None
        assert v1.created_at is not None
        v2 = await uow.briefs.save_version(
            rec.id, f.make_brief(2, summary="two"), change_set_id="CHG-001"
        )
        assert v2.version == 2 and v2.change_set_id == "CHG-001"
    async with store.begin(A) as uow:
        latest = await uow.briefs.get(rec.id)
        assert latest.version == 2 and latest.brief.idea["summary"].value == "two"
        first = await uow.briefs.get(rec.id, 1)
        assert first.brief.idea["summary"].value == "one" and first.change_set_id is None
        await _raises(NotFound, uow.briefs.get(rec.id, 3))
        await _raises(NotFound, uow.briefs.get(rec.id, 0))
        versions = await uow.briefs.list_versions(rec.id)
        assert [v.version for v in versions] == [1, 2]
        assert versions[1].change_set_id == "CHG-001" and versions[0].hash == first.hash
        assert all(v.created_at is not None for v in versions)


@check
async def check_briefs_consecutive_only(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(2)))  # first must be 1
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(0)))
        await uow.briefs.save_version(rec.id, f.make_brief(1))
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(1)))  # stale writer
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(3)))  # skipped version
        await uow.briefs.save_version(rec.id, f.make_brief(2))
    async with store.begin(A) as uow:
        assert [v.version for v in await uow.briefs.list_versions(rec.id)] == [1, 2]
        await _raises(NotFound, uow.briefs.save_version("ses_missing", f.make_brief(1)))


@check
async def check_briefs_concurrent_writers(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)

    async def write(n: int) -> str:
        try:
            async with store.begin(A) as uow:
                await uow.briefs.save_version(rec.id, f.make_brief(1, summary=f"writer {n}"))
            return "ok"
        except Conflict:
            return "conflict"

    results = await asyncio.gather(*(write(i) for i in range(CONCURRENCY)))
    assert results.count("ok") == 1 and results.count("conflict") == CONCURRENCY - 1
    async with store.begin(A) as uow:
        assert [v.version for v in await uow.briefs.list_versions(rec.id)] == [1]


@check
async def check_briefs_hashes(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    brief = f.make_brief(1, summary="hash me")
    async with store.begin(A) as uow:
        saved = await uow.briefs.save_version(rec.id, brief)
        assert saved.hash == brief_hash(brief)
        assert saved.section_hashes == section_hashes(brief)
        other = await uow.briefs.save_version(rec.id, f.make_brief(2, summary="different"))
        assert other.hash != saved.hash
        assert other.section_hashes["idea"] != saved.section_hashes["idea"]
        assert other.section_hashes["scope"] == saved.section_hashes["scope"]
    async with store.begin(A) as uow:
        back = await uow.briefs.get(rec.id, 1)
        assert (
            back.hash == brief_hash(back.brief) == brief_hash(brief)
        )  # the JSON round trip is lossless
        assert back.section_hashes == section_hashes(back.brief)
        assert back.brief.model_dump(mode="json") == brief.model_dump(mode="json")


@check
async def check_briefs_baseline_and_immutability(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await uow.briefs.save_version(rec.id, f.make_brief(1, summary="approved text"))
        await uow.briefs.save_version(rec.id, f.make_brief(2, summary="later text"))
        assert await uow.briefs.get_baseline(rec.id) is None
        await _raises(NotFound, uow.briefs.mark_baseline(rec.id, 9))
        marked = await uow.briefs.mark_baseline(rec.id, 1)
        assert marked.version == 1 and marked.baseline_label == "B-G1"
        again = await uow.briefs.mark_baseline(rec.id, 1)  # idempotent for the same version
        assert again.baseline_label == "B-G1" and again.hash == marked.hash
        await _raises(Conflict, uow.briefs.mark_baseline(rec.id, 2))  # label taken (immutable)
        await _raises(
            Conflict, uow.briefs.mark_baseline(rec.id, 1, "B-G2")
        )  # version already labelled
        other = await uow.briefs.mark_baseline(rec.id, 2, "B-G2")
        assert other.baseline_label == "B-G2"
    async with store.begin(A) as uow:
        base = await uow.briefs.get_baseline(rec.id)
        assert base is not None and base.version == 1
        assert base.brief.idea["summary"].value == "approved text"
        second = await uow.briefs.get_baseline(rec.id, "B-G2")
        assert second is not None and second.version == 2
        assert await uow.briefs.get_baseline(rec.id, "B-G9") is None
        versions = await uow.briefs.list_versions(rec.id)
        assert [v.baseline_label for v in versions] == ["B-G1", "B-G2"]
        # saving more versions never touches the baseline
        await uow.briefs.save_version(rec.id, f.make_brief(3, summary="newest"))
        still = await uow.briefs.get(rec.id, 1)
        assert still.hash == marked.hash and still.brief.idea["summary"].value == "approved text"
        latest = await uow.briefs.get(rec.id)
        assert latest.version == 3 and latest.baseline_label is None


# --------------------------------------------------------------------------- questions, changes, calls


@check
async def check_questions_ledger(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.questions.list(rec.id) == []
        await _raises(NotFound, uow.questions.get(rec.id, "Q-001"))
        await _raises(NotFound, uow.questions.record_answer(rec.id, "Q-001", "answered", "A"))
        await uow.questions.record_asked(rec.id, f.make_card("Q-001"), {"impact": 5, "why": "must"})
        await uow.questions.record_asked(rec.id, f.make_card("Q-002", question="Who uses it?"), {})
        got = await uow.questions.get(rec.id, "Q-001")
        assert (
            got.outcome == "open"
            and got.card.id == "Q-001"
            and got.why == {"impact": 5, "why": "must"}
        )
        assert got.asked_at is not None and got.answered_at is None and got.answer is None
        done = await uow.questions.record_answer(
            rec.id, "Q-001", "answered", {"option": "A", "n": [1, 2]}
        )
        assert done.outcome == "answered" and done.answer == {"option": "A", "n": [1, 2]}
        assert done.answered_at is not None
        skipped = await uow.questions.record_answer(rec.id, "Q-002", "skipped")
        assert skipped.outcome == "skipped" and skipped.answer is None
    async with store.begin(A) as uow:
        listed = await uow.questions.list(rec.id)
        assert [q.card_id for q in listed] == ["Q-001", "Q-002"]
        assert [q.outcome for q in listed] == ["answered", "skipped"]
        assert listed[1].card.question == "Who uses it?"
        # asking the same card again reopens it (the answer stays in the event log)
        await uow.questions.record_asked(rec.id, f.make_card("Q-001"), {"again": True})
        reopened = await uow.questions.get(rec.id, "Q-001")
        assert (
            reopened.outcome == "open"
            and reopened.answer is None
            and reopened.why == {"again": True}
        )
        assert [q.card_id for q in await uow.questions.list(rec.id)] == ["Q-001", "Q-002"]
        await _raises(NotFound, uow.questions.list("ses_missing"))


@check
async def check_change_sets(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.change_sets.list(rec.id) == []
        await _raises(NotFound, uow.change_sets.get(rec.id, "CHG-001"))
        await uow.change_sets.save(
            rec.id, f.make_change_set("CHG-002", base_version=2, new_version=3)
        )
        await uow.change_sets.save(
            rec.id, f.make_change_set("CHG-001", base_version=1, new_version=2)
        )
        await _raises(Conflict, uow.change_sets.save(rec.id, f.make_change_set("CHG-001")))
    async with store.begin(A) as uow:
        got = await uow.change_sets.get(rec.id, "CHG-001")
        assert got.base_version == 1 and got.new_version == 2 and got.ops[0].path == "idea.summary"
        assert got.inverse_ops[0].value == "Old summary" and got.source == "chat"
        assert [c.id for c in await uow.change_sets.list(rec.id)] == ["CHG-001", "CHG-002"]


@check
async def check_call_log(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.call_log.list(rec.id) == []
        for i in range(4):
            await uow.call_log.record(rec.id, f.make_call(f"call_{i}"))
        await uow.call_log.record(
            rec.id,
            CallLogEntry(
                call_id="call_x", spec_id="cmp.answer", outcome="fallback", reason="schema"
            ),
        )
    async with store.begin(A) as uow:
        entries = await uow.call_log.list(rec.id)
        assert [e.call_id for e in entries] == ["call_x", "call_3", "call_2", "call_1", "call_0"]
        assert entries[0].outcome == "fallback" and entries[0].reason == "schema"
        assert entries[0].at is not None
        assert entries[1].tokens_in == 100 and entries[1].cost_micros == 1200
        assert [e.call_id for e in await uow.call_log.list(rec.id, limit=2)] == ["call_x", "call_3"]
        await _raises(NotFound, uow.call_log.list("ses_missing"))


@check
async def check_call_log_does_not_wait_for_session_writers(factory: StoreFactory) -> None:
    """Logging a model call is never queued behind a unit that is busy writing the same session."""
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as writer:
        await writer.events.append_outbound(rec.id, "notice", {"code": "holding the sequence"})

        async def log_call() -> None:
            async with store.begin(A) as logger:
                await logger.call_log.record(rec.id, f.make_call("call_during_write"))

        await asyncio.wait_for(log_call(), timeout=10)
    async with store.begin(A) as uow:
        assert [e.call_id for e in await uow.call_log.list(rec.id)] == ["call_during_write"]


@check
async def check_nul_characters_never_break_a_write(factory: StoreFactory) -> None:
    """User text can contain NUL; PostgreSQL ``jsonb`` and ``text`` reject it, so both stores drop it."""
    store = await factory()
    rec = await _new_session(store, title="t\x00itle")
    sid = rec.id
    brief = f.make_brief(1, summary="sum\x00mary")
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(sid)).title == "title"
        saved = await uow.briefs.save_version(sid, brief)
        assert saved.brief.idea["summary"].value == "summary"
        assert saved.hash == brief_hash(saved.brief)  # the hash is of the content as stored
        await uow.questions.record_asked(sid, f.make_card(question="why\x00?"), {"why": "a\x00b"})
        await uow.call_log.record(sid, f.make_call().model_copy(update={"reason": "r\x00s"}))
        await uow.contributors.upsert(
            sid, f.make_contributor().model_copy(update={"name": "A\x00sha"})
        )
        source = f.make_source().model_copy(update={"name": "na\x00me.txt"})
        chunks = [c.model_copy(update={"text": "te\x00xt"}) for c in f.make_chunks()]
        assert await uow.sources.save_source(sid, source, chunks=chunks)
        change = f.make_change_set().model_copy(update={"summary": "su\x00m"})
        await uow.change_sets.save(sid, change)
        await uow.qa.upsert(
            sid,
            f.make_qa(
                "Q-001",
                answer={"text": "an\x00swer"},
                custom_text="cus\x00tom",
                batch_id="BA\x00T-1",
            ),
        )
        await uow.facts.add(
            sid,
            f.make_fact(statement="sta\x00tement", quote="qu\x00ote", topics=["to\x00pic"]),
        )
        await uow.summaries.put(sid, f.make_summary(through_fact_id="FCT\x00-001"))
        await uow.batches.add(
            sid, f.make_batch(inputs_hash="ha\x00sh", output={"note": "no\x00te"})
        )
    async with store.begin(A) as uow:
        assert (await uow.briefs.get(sid)).hash == saved.hash
        assert (await uow.questions.get(sid, "Q-001")).card.question == "why?"
        assert (await uow.questions.get(sid, "Q-001")).why == {"why": "ab"}
        assert (await uow.call_log.list(sid))[0].reason == "rs"
        assert (await uow.contributors.list(sid))[0].name == "Asha"
        assert (await uow.sources.get_source(sid, "SRC-001")).name == "name.txt"
        assert (await uow.sources.list_chunks(sid, "SRC-001"))[0].text == "text"
        assert (await uow.change_sets.get(sid, "CHG-001")).summary == "sum"
        qa = await uow.qa.get(sid, "Q-001")
        assert qa.answer == {"text": "answer"} and qa.custom_text == "custom"
        assert qa.batch_id == "BAT-1"
        assert [x.card_id for x in await uow.qa.list(sid, batch_id="BA\x00T-1")] == ["Q-001"]
        fact = await uow.facts.get(sid, "FCT-001")
        assert (fact.statement, fact.quote, fact.topics) == ("statement", "quote", ["topic"])
        # the NUL-free fact is what is stored, so writing it back changes nothing and conflicts with nothing
        await uow.facts.update(sid, f.make_fact(statement="sta\x00tement", quote="qu\x00ote"))
        summary = await uow.summaries.get(sid)
        assert summary is not None and summary.through_fact_id == "FCT-001"
        batch = await uow.batches.get(sid, "BAT-001")
        assert batch.inputs_hash == "hash" and batch.output == {"note": "note"}
        assert (await uow.batches.find(sid, "initial", "ha\x00sh")) is not None


# --------------------------------------------------------------------------- sources, contributors


@check
async def check_sources_chunks_claims(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    other = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.sources.list_sources(rec.id) == []
        src = f.make_source("SRC-001", sha256="a" * 64)
        assert (
            await uow.sources.save_source(rec.id, src, chunks=f.make_chunks("SRC-001", 3)) is True
        )
        # same content again (even under another id): de-duplicated, nothing written
        dup = f.make_source("SRC-002", sha256="a" * 64)
        assert (
            await uow.sources.save_source(rec.id, dup, chunks=f.make_chunks("SRC-002", 1)) is False
        )
        # the same sha256 in ANOTHER session is a different source
        assert (
            await uow.sources.save_source(
                other.id, f.make_source("SRC-001", sha256="a" * 64), chunks=[]
            )
            is True
        )
        assert (
            await uow.sources.save_source(
                rec.id, f.make_source("SRC-003", sha256="b" * 64), chunks=[]
            )
            is True
        )
        await _raises(
            Conflict,
            uow.sources.save_source(rec.id, f.make_source("SRC-003", sha256="c" * 64), chunks=[]),
        )
    async with store.begin(A) as uow:
        assert [s.id for s in await uow.sources.list_sources(rec.id)] == ["SRC-001", "SRC-003"]
        got = await uow.sources.get_source(rec.id, "SRC-001")
        assert got.sha256 == "a" * 64 and got.kind == "text"
        chunks = await uow.sources.list_chunks(rec.id, "SRC-001")
        assert [c.index for c in chunks] == [0, 1, 2] and chunks[1].pointer == "page 2"
        assert await uow.sources.list_chunks(rec.id, "SRC-003") == []
        await _raises(NotFound, uow.sources.get_source(rec.id, "SRC-002"))
        await _raises(NotFound, uow.sources.list_chunks(rec.id, "SRC-002"))

        assert await uow.sources.list_claims(rec.id) == []
        await uow.sources.save_claims(rec.id, [f.make_claim("CLM-001"), f.make_claim("CLM-002")])
        changed = f.make_claim("CLM-001").model_copy(update={"status": "used"})
        await uow.sources.save_claims(rec.id, [changed])  # upsert by id
        claims = await uow.sources.list_claims(rec.id)
        assert [(c.id, c.status) for c in claims] == [
            ("CLM-001", "used"),
            ("CLM-002", "needs_confirming"),
        ]
        assert claims[0].statement.quote == "track their orders"
        assert await uow.sources.list_claims(other.id) == []


@check
async def check_contributors(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        assert await uow.contributors.list(rec.id) == []
        await uow.contributors.upsert(rec.id, f.make_contributor("con_1"))
        await uow.contributors.upsert(rec.id, f.make_contributor("con_2", role="approver"))
        await uow.contributors.upsert(rec.id, f.make_contributor("con_1", role="commenter"))
        await _raises(NotFound, uow.contributors.upsert("ses_missing", f.make_contributor()))
    async with store.begin(A) as uow:
        found = await uow.contributors.list(rec.id)
        assert [(c.id, c.role) for c in found] == [("con_1", "commenter"), ("con_2", "approver")]


# --------------------------------------------------------------------------- interview memory (ADR 0015)

IST = timezone(timedelta(hours=5, minutes=30))


def _clock(store: Store) -> FakeClock:
    return cast("FakeClock", getattr(store, "clock"))  # noqa: B009


@check
async def check_qa_record_roundtrip(factory: StoreFactory) -> None:
    """Every field of a Q&A record survives the store, timestamps come back as UTC, falsy answers stay."""
    store = await factory()
    sid = (await _new_session(store)).id
    asked = datetime(2026, 11, 9, 14, 30, 0, 123456, tzinfo=IST)
    answered = asked + timedelta(minutes=2)
    card = f.make_card("Q-007", question="Which city do you start in?")
    full = f.make_qa(
        "Q-007",
        1,
        card=card,
        batch_id="BAT-002",
        group_id="G2",
        mode="followup",
        status="answered",
        answer={"option": "B", "extra": [1, 2.5, None, True]},
        custom_text="পুনে, then Mumbai",
        input_mode="voice",
        parent_card_id="Q-003",
        asked_at=asked,
        answered_at=answered,
    )
    bare = f.make_qa("Q-008", 1, mode=None, batch_id=None, group_id=None)
    falsy: list[JsonValue] = [None, 0, False, "", [], {}, "A", ["A", "B"], {"k": None}]
    async with store.begin(A) as uow:
        await uow.qa.upsert(sid, full)
        await uow.qa.upsert(sid, bare)
        for i, answer in enumerate(falsy):
            await uow.qa.upsert(sid, f.make_qa(f"Q-1{i:02d}", answer=answer, status="answered"))
    async with store.begin(A) as uow:
        got = await uow.qa.get(sid, "Q-007")
        assert got == full.model_copy(
            update={"asked_at": asked.astimezone(UTC), "answered_at": answered.astimezone(UTC)}
        )
        assert got.asked_at == asked and got.asked_at is not None
        assert got.asked_at.utcoffset() == timedelta(0)
        assert got.answered_at is not None and got.answered_at.utcoffset() == timedelta(0)
        assert got.card.question == "Which city do you start in?" and len(got.card.options) == 3
        plain = await uow.qa.get(sid, "Q-008")
        assert plain == bare and plain.asked_at is None and plain.answered_at is None
        assert plain.mode is None and plain.batch_id is None and plain.answer is None
        for i, answer in enumerate(falsy):
            back = (await uow.qa.get(sid, f"Q-1{i:02d}")).answer
            assert back == answer and type(back) is type(answer), (answer, back)


@check
async def check_qa_versions_filters_ordering(factory: StoreFactory) -> None:
    store = await factory()
    sid = (await _new_session(store)).id
    async with store.begin(A) as uow:
        assert await uow.qa.list(sid) == []
        assert await uow.qa.list(sid, latest_only=False) == []
        await _raises(NotFound, uow.qa.get(sid, "Q-001"))
        await _raises(NotFound, uow.qa.get(sid, "Q-001", 1))
        await uow.qa.upsert(sid, f.make_qa("Q-001", 1, batch_id="BAT-001", status="answered"))
        await uow.qa.upsert(sid, f.make_qa("Q-002", 1, batch_id="BAT-001"))
        await uow.qa.upsert(sid, f.make_qa("Q-003", 1, batch_id="BAT-002", mode="followup"))
        # an edit: v2 of Q-001 supersedes v1 (a new row; the old one is marked, never removed)
        await uow.qa.upsert(
            sid,
            f.make_qa("Q-001", 2, batch_id="BAT-001", status="answered", answer="B", mode="replan"),
        )
        await uow.qa.upsert(sid, f.make_qa("Q-001", 1, batch_id="BAT-001", status="superseded"))

    async with store.begin(A) as uow:
        assert (await uow.qa.get(sid, "Q-001")).version == 2  # newest by default
        assert (await uow.qa.get(sid, "Q-001")).answer == "B"
        old = await uow.qa.get(sid, "Q-001", 1)
        assert old.status == "superseded" and old.answer is None
        await _raises(NotFound, uow.qa.get(sid, "Q-001", 3))
        await _raises(NotFound, uow.qa.get(sid, "Q-009"))

        def keys(rows: list[Any]) -> list[tuple[str, int]]:
            return [(r.card_id, r.version) for r in rows]

        # insertion order; replacing (Q-001, 1) did not move it
        every = [("Q-001", 1), ("Q-002", 1), ("Q-003", 1), ("Q-001", 2)]
        assert keys(await uow.qa.list(sid, latest_only=False)) == every
        assert keys(await uow.qa.list(sid)) == [("Q-002", 1), ("Q-003", 1), ("Q-001", 2)]
        # filters apply to each card's newest version; with latest_only=False to every version
        assert keys(await uow.qa.list(sid, status="open")) == [("Q-002", 1), ("Q-003", 1)]
        assert keys(await uow.qa.list(sid, status="answered")) == [("Q-001", 2)]
        assert await uow.qa.list(sid, status="superseded") == []
        superseded = await uow.qa.list(sid, status="superseded", latest_only=False)
        assert keys(superseded) == [("Q-001", 1)]
        assert keys(await uow.qa.list(sid, status="answered", latest_only=False)) == [("Q-001", 2)]
        assert keys(await uow.qa.list(sid, batch_id="BAT-001")) == [("Q-002", 1), ("Q-001", 2)]
        assert keys(await uow.qa.list(sid, batch_id="BAT-001", latest_only=False)) == [
            ("Q-001", 1),
            ("Q-002", 1),
            ("Q-001", 2),
        ]
        assert keys(await uow.qa.list(sid, batch_id="BAT-002", status="open")) == [("Q-003", 1)]
        assert await uow.qa.list(sid, batch_id="BAT-009") == []
        assert (await uow.qa.list(sid, batch_id="BAT-002"))[0].mode == "followup"

        # the same (card, version) is replaced in place, clearing a field that is now empty
        await uow.qa.upsert(
            sid, f.make_qa("Q-002", 1, batch_id="BAT-001", status="answered", answer={"x": 1})
        )
        assert (await uow.qa.get(sid, "Q-002")).answer == {"x": 1}
        await uow.qa.upsert(sid, f.make_qa("Q-002", 1, batch_id="BAT-001", status="skipped"))
        again = await uow.qa.get(sid, "Q-002")
        assert again.status == "skipped" and again.answer is None
        assert keys(await uow.qa.list(sid, latest_only=False)) == every
        assert [r.status for r in await uow.qa.list(sid)] == ["skipped", "open", "answered"]
        await _raises(NotFound, uow.qa.list("ses_missing"))
        await _raises(NotFound, uow.qa.upsert("ses_missing", f.make_qa()))


@check
async def check_facts_ledger(factory: StoreFactory) -> None:
    store = await factory()
    clock = _clock(store)
    sid = (await _new_session(store)).id
    full = f.make_fact(
        "FCT-002",
        type="rule",
        origin="tap",
        confidence=0.35,
        topics=["pricing", "refunds"],
        entities=["tiffin plan"],
        consumers=["blueprint", "design"],
        source_ref="Q-004",
        qa_card_id="Q-004",
        claim_id="CLM-003",
        unverified_quote=True,
        supersedes=["FCT-001"],
        not_used_reason="small talk",
        effects=[
            FactEffect(slot_id="constraints.refunds", value_json='{"days": 7}', mode="add"),
            FactEffect(slot_id="idea.summary"),
        ],
        aspects=["refunds:*", "business_model:who_pays"],
    )
    async with store.begin(A) as uow:
        assert await uow.facts.list(sid) == []
        await _raises(NotFound, uow.facts.get(sid, "FCT-001"))
        await _raises(NotFound, uow.facts.update(sid, f.make_fact("FCT-001")))
        await uow.facts.add(sid, f.make_fact("FCT-001"))
        await uow.facts.add(sid, full)
        await uow.facts.add(sid, f.make_fact("FCT-003", status="disputed"))
        dup = f.make_fact("FCT-001", statement="Something else entirely")
        await _raises(Conflict, uow.facts.add(sid, dup))  # ids are unique; nothing was changed
        await uow.facts.add(
            sid, f.make_fact("FCT-004", created_at=datetime(2026, 1, 2, tzinfo=IST))
        )
        explicit = await uow.facts.get(sid, "FCT-004")
        assert explicit.created_at == datetime(2026, 1, 2, tzinfo=IST)  # an explicit time is kept
        assert explicit.created_at is not None and explicit.created_at.utcoffset() == timedelta(0)
        assert explicit.updated_at == clock.now()  # but updated_at is always the store's

    async with store.begin(A) as uow:
        first = await uow.facts.get(sid, "FCT-001")
        assert first.statement == "Customers pay for a tiffin plan every month"
        assert first.status == "active" and first.created_at == clock.now() == first.updated_at
        assert first.created_at is not None and first.created_at.utcoffset() == timedelta(0)
        got = await uow.facts.get(sid, "FCT-002")
        assert got == full.model_copy(update={"created_at": clock.now(), "updated_at": clock.now()})
        assert [e.mode for e in got.effects] == ["add", "set"]
        assert [x.id for x in await uow.facts.list(sid)] == [
            "FCT-001",
            "FCT-002",
            "FCT-003",
            "FCT-004",
        ]
        assert [x.id for x in await uow.facts.list(sid, status="active")] == [
            "FCT-001",
            "FCT-002",
            "FCT-004",
        ]
        assert [x.id for x in await uow.facts.list(sid, status="disputed")] == ["FCT-003"]
        assert await uow.facts.list(sid, status="superseded") == []
        await _raises(NotFound, uow.facts.get(sid, "FCT-009"))
        await _raises(NotFound, uow.facts.get("ses_missing", "FCT-001"))
        await _raises(NotFound, uow.facts.list("ses_missing"))
        await _raises(NotFound, uow.facts.add("ses_missing", f.make_fact()))

    # update: status, links and metadata change; the order and created_at do not
    created = clock.now()
    clock.advance(60)
    async with store.begin(A) as uow:
        current = await uow.facts.get(sid, "FCT-001")
        changed = current.model_copy(
            update={
                "status": "superseded",
                "confidence": 0.1,
                "topics": ["pricing", "history"],
                "supersedes": [],
                "unverified_quote": True,
                "not_used_reason": "",
                "aspects": [],
                "effects": [],
                "origin": "document",
                "created_at": None,  # None means "unchanged"
                "updated_at": created - timedelta(days=9),  # always replaced by the store
            }
        )
        await uow.facts.update(sid, changed)
        # identity fields cannot change; the unit of work stays usable after the Conflict
        for field, value in (
            ("statement", "A different statement"),
            ("quote", "a different quote"),
            ("created_at", created + timedelta(seconds=1)),
        ):
            err = await _raises(
                Conflict, uow.facts.update(sid, current.model_copy(update={field: value}))
            )
            assert field in str(err) and err.details["fields"] == [field]
        both = current.model_copy(update={"statement": "x", "quote": "y"})
        err2 = await _raises(Conflict, uow.facts.update(sid, both))
        assert err2.details["fields"] == ["statement", "quote"]
        await _raises(NotFound, uow.facts.update(sid, f.make_fact("FCT-009")))
        await _raises(NotFound, uow.facts.update("ses_missing", current))
    async with store.begin(A) as uow:
        after = await uow.facts.get(sid, "FCT-001")
        assert (
            after.status == "superseded" and after.confidence == 0.1 and after.origin == "document"
        )
        assert after.topics == ["pricing", "history"] and after.unverified_quote is True
        assert after.effects == [] and after.aspects == []
        assert after.statement == "Customers pay for a tiffin plan every month"
        assert after.quote == "they pay monthly for the tiffin plan"
        assert after.created_at == created and after.updated_at == clock.now()
        assert (await uow.facts.get(sid, "FCT-002")).updated_at == created  # untouched
        assert [x.id for x in await uow.facts.list(sid)][:3] == ["FCT-001", "FCT-002", "FCT-003"]
        assert [x.id for x in await uow.facts.list(sid, status="superseded")] == ["FCT-001"]
        # an unchanged fact can be written again (the order and the identity fields stay)
        await uow.facts.update(sid, after)
        again = await uow.facts.get(sid, "FCT-001")
        assert again.created_at == created and again.statement == after.statement


@check
async def check_facts_next_id_is_never_reused(factory: StoreFactory) -> None:
    store = await factory()
    sid = (await _new_session(store)).id
    other = (await _new_session(store)).id
    async with store.begin(A) as uow:
        assert await uow.facts.next_id(sid) == "FCT-001"
        assert await uow.facts.next_id(sid) == "FCT-001"  # reads state, reserves nothing
        await uow.facts.add(sid, f.make_fact("FCT-001"))
        assert await uow.facts.next_id(sid) == "FCT-002"
        await uow.facts.add(sid, f.make_fact("FCT-005"))  # a gap: highest + 1, not count + 1
        assert await uow.facts.next_id(sid) == "FCT-006"
        await uow.facts.add(sid, f.make_fact("FCT-006", status="superseded"))
        assert await uow.facts.next_id(sid) == "FCT-007"  # superseded facts still count
        await uow.facts.update(sid, f.make_fact("FCT-006", status="disputed", created_at=None))
        await uow.facts.update(
            sid, (await uow.facts.get(sid, "FCT-006")).model_copy(update={"status": "not_used"})
        )
        assert await uow.facts.next_id(sid) == "FCT-007"
        assert await uow.facts.next_id(other) == "FCT-001"  # numbering is per session
        await uow.facts.add(sid, f.make_fact("FCT-0099"))  # more digits and leading zeros parse
        assert await uow.facts.next_id(sid) == "FCT-100"
        await uow.facts.add(sid, f.make_fact("FCT-999"))
        assert await uow.facts.next_id(sid) == "FCT-1000"
        # a fact id with non-ASCII digits is stored but never counted (and never breaks a numeric cast)
        await uow.facts.add(sid, f.make_fact("FCT-\u0667\u0667\u0667"))
        assert await uow.facts.next_id(sid) == "FCT-1000"
        await _raises(NotFound, uow.facts.next_id("ses_missing"))
    async with store.begin(A) as uow:
        assert await uow.facts.next_id(sid) == "FCT-1000"
        assert await uow.facts.next_id(other) == "FCT-001"


@check
async def check_facts_concurrent_writers_get_distinct_ids(factory: StoreFactory) -> None:
    """``next_id`` then ``add`` in one unit of work is race-free: the session lock serialises writers."""
    store = await factory()
    sid = (await _new_session(store)).id

    async def write(n: int) -> str:
        async with store.begin(A) as uow:
            fact_id = await uow.facts.next_id(sid)
            await uow.facts.add(sid, f.make_fact(fact_id, statement=f"fact from writer {n}"))
            return fact_id

    ids = await asyncio.gather(*(write(i) for i in range(CONCURRENCY)))
    assert sorted(ids) == [f"FCT-{n:03d}" for n in range(1, CONCURRENCY + 1)]
    async with store.begin(A) as uow:
        assert [x.id for x in await uow.facts.list(sid)] == sorted(ids)


@check
async def check_summary_put_get(factory: StoreFactory) -> None:
    store = await factory()
    clock = _clock(store)
    sid = (await _new_session(store)).id
    other = (await _new_session(store)).id
    async with store.begin(A) as uow:
        assert await uow.summaries.get(sid) is None
        await _raises(NotFound, uow.summaries.get("ses_missing"))
        await _raises(NotFound, uow.summaries.put("ses_missing", f.make_summary()))
        stale = datetime(2020, 1, 1, tzinfo=UTC)
        await uow.summaries.put(sid, f.make_summary(1, updated_at=stale))
        await uow.summaries.put(other, f.make_summary(7))
    async with store.begin(A) as uow:
        got = await uow.summaries.get(sid)
        assert got is not None and got.version == 1
        assert got == f.make_summary(1, updated_at=clock.now())  # updated_at is the store's clock
        assert got.topics[0].topic == "pricing" and got.topics[0].fact_ids == ["FCT-001"]
        assert got.through_fact_id == "FCT-001"
    clock.advance(30)
    async with store.begin(A) as uow:  # one row per session: put replaces
        newer = f.make_summary(2, topics=[], through_fact_id="FCT-009")
        await uow.summaries.put(sid, newer)
        again = await uow.summaries.get(sid)
        assert again == newer.model_copy(update={"updated_at": clock.now()})
        assert again is not None and again.topics == [] and again.version == 2
        zero = f.make_summary(0, topics=[], through_fact_id="")
        await uow.summaries.put(sid, zero)
    async with store.begin(A) as uow:
        final = await uow.summaries.get(sid)
        assert final is not None and final.version == 0 and final.through_fact_id == ""
        theirs = await uow.summaries.get(other)
        assert theirs is not None and theirs.version == 7  # other sessions are independent


@check
async def check_batches_ledger(factory: StoreFactory) -> None:
    store = await factory()
    clock = _clock(store)
    sid = (await _new_session(store)).id
    other = (await _new_session(store)).id
    async with store.begin(A) as uow:
        assert await uow.batches.list(sid) == []
        assert await uow.batches.next_id(sid) == "BAT-001"
        assert await uow.batches.find(sid, "initial", "h1") is None
        await _raises(NotFound, uow.batches.get(sid, "BAT-001"))
        await uow.batches.add(sid, f.make_batch("BAT-001", mode="initial", inputs_hash="h1"))
        assert await uow.batches.next_id(sid) == "BAT-002"
        await uow.batches.add(sid, f.make_batch("BAT-002", mode="followup", inputs_hash="h2"))
        await uow.batches.add(sid, f.make_batch("BAT-003", mode="initial", inputs_hash="h3"))
        # a duplicate id, and the same (mode, inputs_hash) under another id, are refused ...
        await _raises(Conflict, uow.batches.add(sid, f.make_batch("BAT-001", inputs_hash="x")))
        await _raises(
            Conflict,
            uow.batches.add(sid, f.make_batch("BAT-004", mode="initial", inputs_hash="h1")),
        )
        # ... but the same hash under another mode, or in another session, is a different run
        await uow.batches.add(sid, f.make_batch("BAT-004", mode="replan", inputs_hash="h1"))
        await uow.batches.add(other, f.make_batch("BAT-001", mode="initial", inputs_hash="h1"))
        assert await uow.batches.next_id(sid) == "BAT-005"

    async with store.begin(A) as uow:
        assert [b.id for b in await uow.batches.list(sid)] == [
            "BAT-001",
            "BAT-002",
            "BAT-003",
            "BAT-004",
        ]
        found = await uow.batches.find(sid, "initial", "h1")
        assert found is not None and found.id == "BAT-001"
        replan = await uow.batches.find(sid, "replan", "h1")
        assert replan is not None and replan.id == "BAT-004"
        assert await uow.batches.find(sid, "initial", "h2") is None
        assert await uow.batches.find(sid, "deepen", "h1") is None
        assert await uow.batches.find(sid, "initial", "H1") is None  # exact match
        mine = await uow.batches.find(other, "initial", "h1")
        assert mine is not None and mine.id == "BAT-001"
        got = await uow.batches.get(sid, "BAT-002")
        assert got == f.make_batch(
            "BAT-002", mode="followup", inputs_hash="h2", created_at=clock.now()
        )
        assert got.output["covered_needs"] == ["audience"] and got.accepted == ["Q-001", "Q-002"]
        assert got.rejected[0].reasons == ["duplicate"] and got.verdicts[0].specific == 4
        assert got.created_at is not None and got.created_at.utcoffset() == timedelta(0)
        await _raises(NotFound, uow.batches.get(sid, "BAT-009"))
        await _raises(NotFound, uow.batches.get("ses_missing", "BAT-001"))
        await _raises(NotFound, uow.batches.list("ses_missing"))
        await _raises(NotFound, uow.batches.find("ses_missing", "initial", "h1"))
        await _raises(NotFound, uow.batches.next_id("ses_missing"))
        await _raises(NotFound, uow.batches.add("ses_missing", f.make_batch("BAT-009")))

    # flags, numbers and an explicit creation time survive; big costs need a 64-bit column
    when = datetime(2026, 11, 9, 20, 0, 0, tzinfo=IST)
    heavy = f.make_batch(
        "BAT-010",
        mode="simplify",
        inputs_hash="h10",
        used_fallback=True,
        repaired=True,
        latency_ms=2_000_000,
        cost_micros=5_000_000_000,
        created_at=when,
        output={"nested": {"list": [1, 2.5, None, True, "x"], "empty": {}}},
        accepted=[],
        rejected=[],
        verdicts=[],
    )
    async with store.begin(A) as uow:
        await uow.batches.add(sid, heavy)
        assert await uow.batches.next_id(sid) == "BAT-011"
    async with store.begin(A) as uow:
        back = await uow.batches.get(sid, "BAT-010")
        assert back == heavy and back.created_at == when
        assert back.created_at is not None and back.created_at.utcoffset() == timedelta(0)
        assert back.used_fallback and back.repaired and back.cost_micros == 5_000_000_000


# --------------------------------------------------------------------------- tenant isolation


@check
async def check_tenant_isolation(factory: StoreFactory) -> None:
    store = await factory()
    rec = await _new_session(store, tenant=A)
    sid = rec.id
    async with store.begin(A) as uow:
        await uow.events.record_inbound(sid, "e1", "user_message", _payload("secret"))
        await uow.events.append_outbound(sid, "assistant_message", _payload("reply"), turn_id="e1")
        await uow.briefs.save_version(sid, f.make_brief(1))
        await uow.questions.record_asked(sid, f.make_card("Q-001"), {})
        await uow.change_sets.save(sid, f.make_change_set())
        await uow.call_log.record(sid, f.make_call())
        await uow.sources.save_source(sid, f.make_source(), chunks=f.make_chunks())
        await uow.sources.save_claims(sid, [f.make_claim()])
        await uow.contributors.upsert(sid, f.make_contributor())
        await uow.qa.upsert(sid, f.make_qa("Q-001"))
        await uow.facts.add(sid, f.make_fact("FCT-001"))
        await uow.summaries.put(sid, f.make_summary(3))
        await uow.batches.add(sid, f.make_batch("BAT-001"))
    await _new_session(store, tenant=B, user_id=f.USER_A)  # same user id, other tenant

    async with store.begin(B) as uow:
        await _raises(NotFound, uow.sessions.get(sid))
        await _raises(NotFound, uow.sessions.save(rec.model_copy(update={"tenant_id": B})))
        await _raises(TenantViolation, uow.sessions.save(rec))  # bound to tenant A, unit bound to B
        await _raises(NotFound, uow.sessions.try_begin_turn(sid))
        await _raises(NotFound, uow.sessions.end_turn(sid))
        await _raises(NotFound, uow.sessions.add_spend(sid, 5))
        await _raises(NotFound, uow.sessions.set_state(sid, "PAUSED"))
        assert sid not in [s.id for s in await uow.sessions.list_for_user(f.USER_A)]
        assert sid not in [
            s.id for s in await uow.sessions.list_for_user(f.USER_A, include_archived=True)
        ]

        await _raises(NotFound, uow.events.record_inbound(sid, "e9", "user_message", _payload("x")))
        await _raises(NotFound, uow.events.append_outbound(sid, "notice", {}))
        await _raises(NotFound, uow.events.list_outbound(sid))
        await _raises(NotFound, uow.events.list_history(sid))
        await _raises(NotFound, uow.events.get_inbound(sid, "e1"))

        await _raises(NotFound, uow.briefs.save_version(sid, f.make_brief(2)))
        await _raises(NotFound, uow.briefs.get(sid))
        await _raises(NotFound, uow.briefs.get(sid, 1))
        await _raises(NotFound, uow.briefs.list_versions(sid))
        await _raises(NotFound, uow.briefs.mark_baseline(sid, 1))
        await _raises(NotFound, uow.briefs.get_baseline(sid))

        await _raises(NotFound, uow.questions.record_asked(sid, f.make_card("Q-009"), {}))
        await _raises(NotFound, uow.questions.record_answer(sid, "Q-001", "answered", "A"))
        await _raises(NotFound, uow.questions.get(sid, "Q-001"))
        await _raises(NotFound, uow.questions.list(sid))

        await _raises(NotFound, uow.change_sets.save(sid, f.make_change_set("CHG-009")))
        await _raises(NotFound, uow.change_sets.get(sid, "CHG-001"))
        await _raises(NotFound, uow.change_sets.list(sid))

        await _raises(NotFound, uow.call_log.record(sid, f.make_call("call_b")))
        await _raises(NotFound, uow.call_log.list(sid))

        await _raises(NotFound, uow.sources.save_source(sid, f.make_source("SRC-009"), chunks=[]))
        await _raises(NotFound, uow.sources.get_source(sid, "SRC-001"))
        await _raises(NotFound, uow.sources.list_sources(sid))
        await _raises(NotFound, uow.sources.list_chunks(sid, "SRC-001"))
        await _raises(NotFound, uow.sources.save_claims(sid, [f.make_claim("CLM-009")]))
        await _raises(NotFound, uow.sources.list_claims(sid))

        await _raises(NotFound, uow.contributors.upsert(sid, f.make_contributor("con_9")))
        await _raises(NotFound, uow.contributors.list(sid))

        await _raises(NotFound, uow.qa.upsert(sid, f.make_qa("Q-009")))
        await _raises(NotFound, uow.qa.get(sid, "Q-001"))
        await _raises(NotFound, uow.qa.get(sid, "Q-001", 1))
        await _raises(NotFound, uow.qa.list(sid))
        await _raises(NotFound, uow.qa.list(sid, status="open", latest_only=False))

        await _raises(NotFound, uow.facts.add(sid, f.make_fact("FCT-009")))
        await _raises(NotFound, uow.facts.update(sid, f.make_fact("FCT-001", status="disputed")))
        await _raises(NotFound, uow.facts.get(sid, "FCT-001"))
        await _raises(NotFound, uow.facts.list(sid))
        await _raises(NotFound, uow.facts.next_id(sid))

        await _raises(NotFound, uow.summaries.get(sid))
        await _raises(NotFound, uow.summaries.put(sid, f.make_summary(9)))

        await _raises(NotFound, uow.batches.add(sid, f.make_batch("BAT-009")))
        await _raises(NotFound, uow.batches.get(sid, "BAT-001"))
        await _raises(NotFound, uow.batches.find(sid, "initial", "hash-bat-001"))
        await _raises(NotFound, uow.batches.list(sid))
        await _raises(NotFound, uow.batches.next_id(sid))

    # tenant A's data is exactly as it was
    async with store.begin(A) as uow:
        got = await uow.sessions.get(sid)
        assert got.row_version == 0 and got.outbound_seq == 1 and got.spent_micros == 0
        assert got.active_turn is False and got.state == "WELCOME"
        assert [e.type for e in (await uow.events.list_history(sid)).items] == [
            "user_message",
            "assistant_message",
        ]
        assert [v.version for v in await uow.briefs.list_versions(sid)] == [1]
        assert (await uow.briefs.get_baseline(sid)) is None
        assert [q.card_id for q in await uow.questions.list(sid)] == ["Q-001"]
        assert [c.id for c in await uow.change_sets.list(sid)] == ["CHG-001"]
        assert len(await uow.call_log.list(sid)) == 1
        assert [s.id for s in await uow.sources.list_sources(sid)] == ["SRC-001"]
        assert [c.id for c in await uow.sources.list_claims(sid)] == ["CLM-001"]
        assert [c.id for c in await uow.contributors.list(sid)] == ["con_1"]
        assert [(q.card_id, q.status) for q in await uow.qa.list(sid)] == [("Q-001", "open")]
        assert [x.id for x in await uow.facts.list(sid)] == ["FCT-001"]
        assert (await uow.facts.get(sid, "FCT-001")).status == "active"
        assert await uow.facts.next_id(sid) == "FCT-002"
        summary = await uow.summaries.get(sid)
        assert summary is not None and summary.version == 3
        assert [b.id for b in await uow.batches.list(sid)] == ["BAT-001"]
        assert await uow.batches.next_id(sid) == "BAT-002"
    # the other tenant never sees it either, and has its own empty memory in its own session
    other = await _new_session(store, tenant=B)
    async with store.begin(B) as uow:
        assert await uow.qa.list(other.id) == []
        assert await uow.facts.list(other.id) == []
        assert await uow.summaries.get(other.id) is None
        assert await uow.batches.list(other.id) == []
        assert await uow.facts.next_id(other.id) == "FCT-001"
        await _raises(NotFound, uow.facts.get(other.id, "FCT-001"))


# --------------------------------------------------------------------------- transactions


@check
async def check_unit_of_work_rollback(factory: StoreFactory) -> None:
    store = await factory()
    keep = await _new_session(store)
    doomed = f.make_session(tenant_id=A)

    class Boom(RuntimeError):
        pass

    try:
        async with store.begin(A) as uow:
            await uow.sessions.create(doomed)
            await uow.events.record_inbound(doomed.id, "e1", "user_message", _payload("lost"))
            await uow.events.append_outbound(doomed.id, "notice", {"code": "lost"})
            await uow.briefs.save_version(doomed.id, f.make_brief(1))
            await uow.questions.record_asked(doomed.id, f.make_card(), {})
            await uow.sources.save_source(doomed.id, f.make_source(), chunks=f.make_chunks())
            await uow.qa.upsert(doomed.id, f.make_qa("Q-001"))
            await uow.facts.add(doomed.id, f.make_fact("FCT-001"))
            await uow.summaries.put(doomed.id, f.make_summary())
            await uow.batches.add(doomed.id, f.make_batch("BAT-001"))
            await uow.sessions.add_spend(keep.id, 999)
            await uow.events.append_outbound(keep.id, "notice", {"code": "also lost"})
            await uow.briefs.save_version(keep.id, f.make_brief(1))
            await uow.qa.upsert(keep.id, f.make_qa("Q-001"))
            await uow.qa.upsert(keep.id, f.make_qa("Q-001", 2))
            await uow.facts.add(keep.id, f.make_fact("FCT-001"))
            await uow.facts.update(keep.id, f.make_fact("FCT-001", status="superseded"))
            await uow.facts.add(keep.id, f.make_fact("FCT-002"))
            await uow.summaries.put(keep.id, f.make_summary(5))
            await uow.batches.add(keep.id, f.make_batch("BAT-001"))
            raise Boom
    except Boom:
        pass

    async with store.begin(A) as uow:
        await _raises(NotFound, uow.sessions.get(doomed.id))
        got = await uow.sessions.get(keep.id)
        assert got.spent_micros == 0 and got.outbound_seq == 0
        assert (await uow.events.list_history(keep.id)).items == []
        assert await uow.briefs.list_versions(keep.id) == []
        # nothing of the interview memory survived either
        assert await uow.qa.list(keep.id, latest_only=False) == []
        assert await uow.facts.list(keep.id) == []
        assert await uow.summaries.get(keep.id) is None
        assert await uow.batches.list(keep.id) == []
        # the rolled-back numbers are free again (nothing was committed, so nothing is reused)
        assert await uow.facts.next_id(keep.id) == "FCT-001"
        assert await uow.batches.next_id(keep.id) == "BAT-001"
        # the rolled-back sequence number is reused (no gap in the SSE ids)
        assert await uow.events.append_outbound(keep.id, "notice", {"code": "kept"}) == 1
        assert await uow.briefs.save_version(keep.id, f.make_brief(1)) is not None


@check
async def check_unit_of_work_commit_and_rollback_calls(factory: StoreFactory) -> None:
    store = await factory()
    first = f.make_session(tenant_id=A)
    second = f.make_session(tenant_id=A)
    third = f.make_session(tenant_id=A)

    try:
        async with store.begin(A) as uow:
            await uow.sessions.create(first)
            await uow.facts.add(first.id, f.make_fact("FCT-001"))
            await uow.commit()  # durable from here on
            await uow.sessions.create(second)
            await uow.facts.add(second.id, f.make_fact("FCT-001"))
            await uow.facts.add(first.id, f.make_fact("FCT-002"))
            await uow.rollback()  # explicit rollback undoes only the work after the commit
            await uow.sessions.create(third)
            await uow.events.record_inbound(third.id, "e1", "user_message", _payload("x"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(first.id)).id == first.id
        assert [x.id for x in await uow.facts.list(first.id)] == ["FCT-001"]
        await _raises(NotFound, uow.sessions.get(second.id))
        await _raises(NotFound, uow.sessions.get(third.id))


@check
async def check_failed_write_does_not_poison_unit(factory: StoreFactory) -> None:
    """A Conflict raised by a repository leaves the unit of work usable for earlier committed work."""
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await uow.briefs.save_version(rec.id, f.make_brief(1))
        await uow.facts.add(rec.id, f.make_fact("FCT-001"))
        await uow.batches.add(rec.id, f.make_batch("BAT-001"))
        await uow.commit()
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(1)))
        await _raises(Conflict, uow.facts.add(rec.id, f.make_fact("FCT-001")))
        await _raises(Conflict, uow.batches.add(rec.id, f.make_batch("BAT-001")))
        await _raises(
            Conflict, uow.batches.add(rec.id, f.make_batch("BAT-002", inputs_hash="hash-bat-001"))
        )
        await _raises(Conflict, uow.facts.update(rec.id, f.make_fact("FCT-001", statement="new")))
        await uow.sessions.add_spend(rec.id, 10)  # still usable after the Conflict
        await uow.facts.add(rec.id, f.make_fact("FCT-002"))
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(rec.id)).spent_micros == 10
        assert [v.version for v in await uow.briefs.list_versions(rec.id)] == [1]
        assert [x.id for x in await uow.facts.list(rec.id)] == ["FCT-001", "FCT-002"]
        assert [b.id for b in await uow.batches.list(rec.id)] == ["BAT-001"]
