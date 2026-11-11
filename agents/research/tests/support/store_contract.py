"""The shared contract checks every Store implementation must pass (in-memory and PostgreSQL run the SAME list).

Each check is ``async def check_x(store: Store) -> None`` and raises AssertionError on a violation. The test modules
for each store call ``run_checks`` (a fresh store per check) so the two implementations cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from mde_research_agent.domain.events import Counters, Event, EventType
from mde_research_agent.store.ports import Decision, RunActive, RunRow, Store

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def row(
    run_id: str = "rrn_1",
    *,
    tenant: str = "t1",
    program: str = "prog_1",
    status: str = "queued",
    at: int = 0,
) -> RunRow:
    return RunRow(
        run_id=run_id,
        tenant_id=tenant,
        program_id=program,
        status=status,
        brief_hash="h" * 64,
        created_at=T0 + timedelta(seconds=at),
    )


def event(run_id: str, seq: int, type_: EventType = "query.issued") -> Event:
    return Event(
        id=f"evt_{run_id}_{seq}",
        seq=seq,
        ts=T0 + timedelta(seconds=seq),
        run_id=run_id,
        type=type_,
        label=f"event {seq}",
        detail={"query": "q"},
        counters=Counters(searches_used=seq),
    )


async def check_create_and_get(store: Store) -> None:
    await store.create_run(row())
    got = await store.get_run("t1", "rrn_1")
    assert got is not None
    assert got.program_id == "prog_1"
    assert got.status == "queued"
    assert await store.get_run("t2", "rrn_1") is None, "another tenant must not see the run"
    assert await store.get_run("t1", "rrn_missing") is None


async def check_one_active_run_per_program(store: Store) -> None:
    await store.create_run(row("rrn_1"))
    with pytest.raises(RunActive) as err:
        await store.create_run(row("rrn_2"))
    assert err.value.run_id == "rrn_1"
    await store.create_run(row("rrn_3", program="prog_2"))  # another program is fine
    await store.create_run(row("rrn_4", tenant="t2"))  # another tenant is fine
    await store.update_run("t1", "rrn_1", status="complete", finished_at=T0 + timedelta(minutes=1))
    await store.create_run(row("rrn_5", at=10))  # the first one finished


async def check_update_and_active(store: Store) -> None:
    await store.create_run(row())
    await store.update_run(
        "t1", "rrn_1", status="running", started_at=T0, load_class="standard", load_score=27
    )
    got = await store.get_run("t1", "rrn_1")
    assert got is not None
    assert got.status == "running"
    assert got.load_class == "standard"
    assert got.load_score == 27
    act = await store.active_run("t1", "prog_1")
    assert act is not None
    assert act.run_id == "rrn_1"
    await store.update_run("t1", "rrn_1", status="partial")
    assert await store.active_run("t1", "prog_1") is None
    with pytest.raises(Exception):  # noqa: B017  (NotFound for a foreign tenant)
        await store.update_run("t2", "rrn_1", status="complete")


async def check_events_are_contiguous_and_replayable(store: Store) -> None:
    await store.create_run(row())
    for i in range(1, 6):
        await store.append_event(event("rrn_1", i))
    assert await store.last_seq("rrn_1") == 5
    assert [e.seq for e in await store.events_after("rrn_1", 0)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in await store.events_after("rrn_1", 3)] == [4, 5]
    assert [e.seq for e in await store.events_after("rrn_1", 0, limit=2)] == [1, 2]
    assert await store.events_after("rrn_1", 5) == []
    assert (await store.events_after("rrn_1", 4))[0].counters.searches_used == 5
    with pytest.raises(Exception):  # noqa: B017  (a gap in seq is a bug)
        await store.append_event(event("rrn_1", 9))


async def check_wait_for_events_wakes_up(store: Store) -> None:
    import asyncio

    await store.create_run(row())
    await store.append_event(event("rrn_1", 1))

    async def later() -> None:
        await asyncio.sleep(0.05)
        await store.append_event(event("rrn_1", 2))

    task = asyncio.create_task(later())
    await store.wait_for_events("rrn_1", 1, timeout=5.0)
    assert await store.last_seq("rrn_1") == 2
    await task
    await store.wait_for_events(
        "rrn_1", 2, timeout=0.05
    )  # nothing new: returns after the timeout, never hangs


async def check_results_are_immutable_and_latest_wins(store: Store) -> None:
    await store.create_run(row("rrn_1"))
    await store.update_run(
        "t1",
        "rrn_1",
        status="complete",
        finished_at=T0 + timedelta(minutes=1),
        result_status="complete",
    )
    assert await store.latest_committed("t1", "prog_1") is None
    await store.save_result(
        "rrn_1", {"items": [{"id": "RI-001"}], "schema": "research.v5"}, {"pages": []}, "hash1"
    )
    assert (await store.get_result("rrn_1")) == {
        "items": [{"id": "RI-001"}],
        "schema": "research.v5",
    }
    assert await store.get_evidence("rrn_1") == {"pages": []}
    with pytest.raises(Exception):  # noqa: B017
        await store.save_result("rrn_1", {"items": []}, {}, "hash2")
    latest = await store.latest_committed("t1", "prog_1")
    assert latest is not None
    assert latest.run_id == "rrn_1"
    assert latest.content_hash == "hash1"
    await store.create_run(row("rrn_2", at=100))
    await store.update_run("t1", "rrn_2", status="partial", finished_at=T0 + timedelta(minutes=9))
    await store.save_result("rrn_2", {"items": []}, {}, "hash3")
    latest2 = await store.latest_committed("t1", "prog_1")
    assert latest2 is not None
    assert latest2.run_id == "rrn_2"
    assert await store.latest_committed("t2", "prog_1") is None


async def check_decisions_are_an_overlay(store: Store) -> None:
    await store.create_run(row())
    await store.add_decision(
        Decision(run_id="rrn_1", item_id="RI-003", kind="reject", reason="wrong", at=T0)
    )
    await store.add_decision(
        Decision(run_id="rrn_1", item_id="RI-004", kind="reject", at=T0 + timedelta(seconds=1))
    )
    got = await store.list_decisions("rrn_1")
    assert [d.item_id for d in got] == ["RI-003", "RI-004"]
    assert got[0].reason == "wrong"
    assert await store.list_decisions("rrn_other") == []


async def check_restart_marks_active_runs_interrupted(store: Store) -> None:
    await store.create_run(row("rrn_1"))
    await store.update_run("t1", "rrn_1", status="running")
    await store.create_run(row("rrn_2", program="prog_2"))
    await store.update_run("t1", "rrn_2", status="complete")
    assert await store.mark_interrupted() == 1
    got = await store.get_run("t1", "rrn_1")
    assert got is not None
    assert got.status == "interrupted"
    done = await store.get_run("t1", "rrn_2")
    assert done is not None
    assert done.status == "complete"


async def check_one_active_run_is_also_enforced_on_update(store: Store) -> None:
    await store.create_run(row("rrn_1", status="complete"))
    await store.create_run(row("rrn_2", at=1))  # active
    with pytest.raises(RunActive) as err:
        await store.update_run("t1", "rrn_1", status="running")  # would be a second active run
    assert err.value.run_id == "rrn_2"
    await store.update_run("t1", "rrn_2", status="partial")
    await store.update_run("t1", "rrn_1", status="running")  # now it is the only one


async def check_list_runs_and_active_runs(store: Store) -> None:
    await store.create_run(row("rrn_1", at=0))
    await store.update_run("t1", "rrn_1", status="complete")
    await store.create_run(row("rrn_2", program="prog_2", at=5))
    await store.create_run(row("rrn_3", tenant="t2", at=6))
    listed = await store.list_runs("t1")
    assert [r.run_id for r in listed] == ["rrn_2", "rrn_1"]  # newest first, own tenant only
    assert [r.run_id for r in await store.list_runs("t1", limit=1)] == ["rrn_2"]
    assert sorted(r.run_id for r in await store.active_runs()) == ["rrn_2", "rrn_3"]  # every tenant


async def check_events_after_pages_in_order(store: Store) -> None:
    await store.create_run(row("rrn_1"))
    for i in range(1, 8):
        await store.append_event(event("rrn_1", i))
    assert [e.seq for e in await store.events_after("rrn_1", 0, limit=3)] == [1, 2, 3]
    assert [e.seq for e in await store.events_after("rrn_1", 3, limit=3)] == [4, 5, 6]
    assert [e.seq for e in await store.events_after("rrn_1", 6)] == [7]
    assert await store.events_after("rrn_1", 7) == []
    assert await store.events_after("rrn_missing", 0) == []
    assert await store.last_seq("rrn_1") == 7


CHECKS: list[Callable[[Store], Awaitable[None]]] = [
    check_create_and_get,
    check_one_active_run_per_program,
    check_update_and_active,
    check_events_are_contiguous_and_replayable,
    check_wait_for_events_wakes_up,
    check_results_are_immutable_and_latest_wins,
    check_decisions_are_an_overlay,
    check_restart_marks_active_runs_interrupted,
    check_one_active_run_is_also_enforced_on_update,
    check_list_runs_and_active_runs,
    check_events_after_pages_in_order,
]
