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
from typing import Any, TypeVar, cast

from pydantic import JsonValue

from mde_compass.domain.hashing import brief_hash, section_hashes
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
        # LIKE wildcards in the query are plain text
        assert (await uow.events.list_history(rec.id, q="%")).items == []


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
            await uow.sessions.add_spend(keep.id, 999)
            await uow.events.append_outbound(keep.id, "notice", {"code": "also lost"})
            await uow.briefs.save_version(keep.id, f.make_brief(1))
            raise Boom
    except Boom:
        pass

    async with store.begin(A) as uow:
        await _raises(NotFound, uow.sessions.get(doomed.id))
        got = await uow.sessions.get(keep.id)
        assert got.spent_micros == 0 and got.outbound_seq == 0
        assert (await uow.events.list_history(keep.id)).items == []
        assert await uow.briefs.list_versions(keep.id) == []
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
            await uow.commit()  # durable from here on
            await uow.sessions.create(second)
            await uow.rollback()  # explicit rollback undoes only the work after the commit
            await uow.sessions.create(third)
            await uow.events.record_inbound(third.id, "e1", "user_message", _payload("x"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(first.id)).id == first.id
        await _raises(NotFound, uow.sessions.get(second.id))
        await _raises(NotFound, uow.sessions.get(third.id))


@check
async def check_failed_write_does_not_poison_unit(factory: StoreFactory) -> None:
    """A Conflict raised by a repository leaves the unit of work usable for earlier committed work."""
    store = await factory()
    rec = await _new_session(store)
    async with store.begin(A) as uow:
        await uow.briefs.save_version(rec.id, f.make_brief(1))
        await uow.commit()
        await _raises(Conflict, uow.briefs.save_version(rec.id, f.make_brief(1)))
        await uow.sessions.add_spend(rec.id, 10)  # still usable after the Conflict
    async with store.begin(A) as uow:
        assert (await uow.sessions.get(rec.id)).spent_micros == 10
        assert [v.version for v in await uow.briefs.list_versions(rec.id)] == [1]
