"""Behaviour specific to the in-memory fake (the shared contract suite covers the rest)."""

from __future__ import annotations

import asyncio

import pytest

from mde_compass.store import testing as f
from mde_compass.store.common import STALE_TURN_SECONDS, payload_text
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


def test_stale_turn_threshold_is_two_minutes() -> None:
    assert STALE_TURN_SECONDS == 120


def test_the_engine_helper_refuses_other_databases() -> None:
    with pytest.raises(ValueError, match="PostgreSQL only"):
        create_store_engine("mysql://u:p@localhost/db")
