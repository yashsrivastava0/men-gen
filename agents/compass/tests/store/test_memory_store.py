"""Behaviour specific to the in-memory fake (the shared contract suite covers the rest)."""

from __future__ import annotations

import asyncio

import pytest

from mde_compass.store import testing as f
from mde_compass.store.common import (
    STALE_TURN_SECONDS,
    id_number,
    next_numbered_id,
    payload_text,
)
from mde_compass.store.memory import InMemoryStore
from mde_compass.store.postgres import create_store_engine
from mde_core.clock import FakeClock
from mde_core.errors import NotFound

A = f.TENANT_A


async def test_open_units_are_counted_and_released_on_error() -> None:
    store = InMemoryStore(FakeClock())
    assert store.open_units == 0 and store.begin_count == 0
    async with store.begin(A):
        assert store.open_units == 1
        async with store.begin(A):
            assert store.open_units == 2
    assert store.open_units == 0 and store.peak_open_units == 2 and store.begin_count == 2
    with pytest.raises(RuntimeError):
        async with store.begin(A):
            raise RuntimeError("boom")
    assert store.open_units == 0


async def test_default_clock_is_a_fake_clock() -> None:
    store = InMemoryStore()
    assert isinstance(store.clock, FakeClock)


async def test_a_failed_unit_releases_its_session_lock() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())

    async def failing_writer() -> None:
        async with store.begin(A) as uow:
            await uow.events.append_outbound(rec.id, "notice", {})
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await failing_writer()

    async def next_writer() -> int:
        async with store.begin(A) as uow:
            return await uow.events.append_outbound(rec.id, "notice", {})

    assert await asyncio.wait_for(next_writer(), timeout=2) == 1  # not blocked, seq reused


async def test_a_second_writer_waits_for_the_open_unit() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())
    order: list[str] = []

    async def second() -> int:
        async with store.begin(A) as uow:
            seq = await uow.events.append_outbound(rec.id, "notice", {"who": "second"})
            order.append("second wrote")
            return seq

    async with store.begin(A) as first:
        assert await first.events.append_outbound(rec.id, "notice", {"who": "first"}) == 1
        task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not task.done()  # queued behind the first unit's row lock
        order.append("first done")
    assert await asyncio.wait_for(task, timeout=2) == 2
    assert order == ["first done", "second wrote"]


async def test_returned_records_are_copies() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session(title="original"))
        rec.title = "mutated without saving"
        again = await uow.sessions.get(rec.id)
        assert again.title == "original"
        again.contributors.append(f.make_contributor())
        assert (await uow.sessions.get(rec.id)).contributors == []


async def test_memory_records_are_copies_and_input_is_not_aliased() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())
        fact = f.make_fact()
        await uow.facts.add(rec.id, fact)
        fact.topics.append("mutated after add")
        got = await uow.facts.get(rec.id, "FCT-001")
        assert got.topics == ["pricing"]
        got.topics.append("mutated after get")
        got.effects.clear()
        again = await uow.facts.get(rec.id, "FCT-001")
        assert again.topics == ["pricing"] and len(again.effects) == 1

        qa = f.make_qa()
        await uow.qa.upsert(rec.id, qa)
        qa.card.options.clear()
        (await uow.qa.get(rec.id, "Q-001")).card.options.clear()
        assert len((await uow.qa.get(rec.id, "Q-001")).card.options) == 3

        await uow.batches.add(rec.id, f.make_batch())
        (await uow.batches.get(rec.id, "BAT-001")).output["groups"] = "mutated"
        assert (await uow.batches.get(rec.id, "BAT-001")).output["groups"] != "mutated"


async def test_next_id_waits_for_the_open_unit_and_follows_its_fact() -> None:
    """``next_id`` takes the session's row lock: a second writer queues behind the first and then sees
    the first one's fact (Postgres: ``SELECT ... FOR NO KEY UPDATE`` on the session row)."""
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())

    async def second() -> str:
        async with store.begin(A) as uow:
            return await uow.facts.next_id(rec.id)

    async with store.begin(A) as first:
        assert await first.facts.next_id(rec.id) == "FCT-001"
        task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not task.done()  # queued behind the first unit's lock
        await first.facts.add(rec.id, f.make_fact("FCT-001"))
    assert await asyncio.wait_for(task, timeout=2) == "FCT-002"


async def test_a_failed_memory_write_releases_the_lock_and_keeps_the_numbers() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())

    async def failing_writer() -> None:
        async with store.begin(A) as uow:
            await uow.facts.add(rec.id, f.make_fact("FCT-001"))
            await uow.batches.add(rec.id, f.make_batch("BAT-001"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await failing_writer()

    async def next_ids() -> tuple[str, str]:
        async with store.begin(A) as uow:
            return await uow.facts.next_id(rec.id), await uow.batches.next_id(rec.id)

    assert await asyncio.wait_for(next_ids(), timeout=2) == ("FCT-001", "BAT-001")


async def test_test_conveniences_bypass_tenant_scope() -> None:
    store = InMemoryStore(FakeClock())
    async with store.begin(A) as uow:
        rec = await uow.sessions.create(f.make_session())
        await uow.events.record_inbound(rec.id, "e1", "user_message", {"text": "hi"})
    assert [s.id for s in store.all_sessions()] == [rec.id]
    assert [e.event_id for e in store.stored_events(rec.id)] == ["e1"]
    async with store.begin(f.TENANT_B) as uow:
        with pytest.raises(NotFound):
            await uow.sessions.get(rec.id)


def test_payload_text_searches_string_values_only() -> None:
    payload = {
        "text": "Hello WORLD",
        "n": 5,
        "nested": {"list": ["Árvíztűrő", 3, None, {"k": "v"}]},
    }
    text = payload_text(payload)
    assert "hello world" in text and "árvíztűrő" in text and "v" in text
    assert "text" not in text and "nested" not in text and "5" not in text
    assert payload_text({}) == "" and payload_text([]) == ""


def test_numbered_ids_follow_the_highest_number() -> None:
    assert next_numbered_id("FCT-", []) == "FCT-001"
    assert next_numbered_id("FCT-", ["FCT-001", "FCT-007", "FCT-003"]) == "FCT-008"
    assert next_numbered_id("FCT-", ["FCT-0099", "FCT-999"]) == "FCT-1000"
    assert next_numbered_id("BAT-", ["FCT-050", "BAT-002"]) == "BAT-003"
    # ids of any other shape are never counted
    for odd in ("FCT-", "FCT-x1", "fct-005", "FCT-1\n", "FCT-\u0667", " FCT-009"):
        assert id_number("FCT-", odd) is None, odd
    assert next_numbered_id("FCT-", ["FCT-002", "FCT-\u0669\u0669\u0669"]) == "FCT-003"
    assert id_number("FCT-", "FCT-0042") == 42


def test_stale_turn_threshold_is_two_minutes() -> None:
    assert STALE_TURN_SECONDS == 120


def test_the_engine_helper_refuses_other_databases() -> None:
    with pytest.raises(ValueError, match="PostgreSQL only"):
        create_store_engine("mysql://u:p@localhost/db")
