"""The shared store contract suite against the real PostgresStore (ephemeral database), plus the Postgres-only
guarantees: concurrency, tenant isolation, UTC, NUL characters, no leaked connections, no leaked password."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone

import psycopg
import pytest
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine
from support.store_contract import CHECKS, T0, event, row

from mde_core.clock import FakeClock
from mde_core.errors import Conflict, NotFound
from mde_research_agent.store.ports import Decision, RunActive, Store
from mde_research_agent.store.postgres import PostgresStore, create_store_engine, normalize_url

pytestmark = pytest.mark.integration


def _dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_postgres_store_meets_the_contract(
    check, postgres_store: PostgresStore, research_engine: AsyncEngine
) -> None:
    store: Store = postgres_store
    await check(store)
    assert research_engine.pool.checkedout() == 0  # type: ignore[attr-defined]  # no connection leaked


# --------------------------------------------------------------------------- concurrency


async def test_concurrent_create_run_for_one_program_has_exactly_one_winner(
    make_postgres_store, postgres_store: PostgresStore
) -> None:
    stores = [make_postgres_store() for _ in range(4)]
    outcomes = await asyncio.gather(
        *(stores[i % 4].create_run(row(f"rrn_{i}")) for i in range(8)), return_exceptions=True
    )
    winners = [i for i, o in enumerate(outcomes) if o is None]
    losers = [o for o in outcomes if isinstance(o, RunActive)]
    assert len(winners) == 1, outcomes
    assert len(losers) == 7, outcomes
    assert {o.run_id for o in losers} == {f"rrn_{winners[0]}"}  # every loser names the winner
    active = await postgres_store.active_run("t1", "prog_1")
    assert active is not None and active.run_id == f"rrn_{winners[0]}"


async def test_concurrent_create_run_for_different_programs_all_succeed(
    postgres_store: PostgresStore,
) -> None:
    outcomes = await asyncio.gather(
        *(postgres_store.create_run(row(f"rrn_{i}", program=f"prog_{i}")) for i in range(5)),
        return_exceptions=True,
    )
    assert outcomes == [None] * 5


async def test_creating_a_run_id_twice_is_a_conflict_not_run_active(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row("rrn_1", status="complete"))
    with pytest.raises(Conflict) as err:
        await postgres_store.create_run(row("rrn_1", status="complete"))
    assert not isinstance(err.value, RunActive)


async def test_concurrent_append_event_with_the_same_seq_has_exactly_one_winner(
    make_postgres_store, postgres_store: PostgresStore
) -> None:
    await postgres_store.create_run(row())
    await postgres_store.append_event(event("rrn_1", 1))
    stores = [make_postgres_store() for _ in range(4)]
    first = event("rrn_1", 2, "query.issued")
    outcomes = await asyncio.gather(
        *(stores[i].append_event(first.model_copy(update={"id": f"evt_w{i}"})) for i in range(4)),
        return_exceptions=True,
    )
    assert sum(o is None for o in outcomes) == 1, outcomes
    assert sum(isinstance(o, ValueError) for o in outcomes) == 3, outcomes
    assert await postgres_store.last_seq("rrn_1") == 2
    assert [e.seq for e in await postgres_store.events_after("rrn_1", 0)] == [1, 2]


async def test_concurrent_first_events_never_leave_a_gap(
    make_postgres_store, postgres_store: PostgresStore
) -> None:
    await postgres_store.create_run(row())
    stores = [make_postgres_store() for _ in range(3)]
    # seq 1, 2, 3 raced in a scrambled order: whatever wins, the log stays 1..k with no gap or duplicate
    outcomes = await asyncio.gather(
        stores[0].append_event(event("rrn_1", 3)),
        stores[1].append_event(event("rrn_1", 1)),
        stores[2].append_event(event("rrn_1", 2)),
        return_exceptions=True,
    )
    assert all(o is None or isinstance(o, ValueError) for o in outcomes), outcomes
    seqs = [e.seq for e in await postgres_store.events_after("rrn_1", 0)]
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) >= 1


async def test_a_long_sequential_log_replays_in_order(postgres_store: PostgresStore) -> None:
    await postgres_store.create_run(row())
    for i in range(1, 61):
        await postgres_store.append_event(event("rrn_1", i))
    assert await postgres_store.last_seq("rrn_1") == 60
    page = await postgres_store.events_after("rrn_1", 0)
    assert [e.seq for e in page] == list(range(1, 61))
    assert [e.seq for e in await postgres_store.events_after("rrn_1", 55, limit=100)] == [
        56,
        57,
        58,
        59,
        60,
    ]
    assert await postgres_store.events_after("rrn_1", 0, limit=0) == []


async def test_concurrent_save_result_has_exactly_one_winner(
    make_postgres_store, postgres_store: PostgresStore
) -> None:
    await postgres_store.create_run(row())
    stores = [make_postgres_store() for _ in range(3)]
    outcomes = await asyncio.gather(
        *(stores[i].save_result("rrn_1", {"i": i}, {}, f"hash{i}") for i in range(3)),
        return_exceptions=True,
    )
    assert sum(o is None for o in outcomes) == 1, outcomes
    assert sum(isinstance(o, ValueError) for o in outcomes) == 2, outcomes
    winner = outcomes.index(None)
    assert await postgres_store.get_result("rrn_1") == {"i": winner}
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None and got.content_hash == f"hash{winner}"


# --------------------------------------------------------------------------- results and events


async def test_a_refused_second_result_leaves_the_run_hash_alone(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    await postgres_store.save_result("rrn_1", {"a": 1}, {"e": 1}, "hash1")
    with pytest.raises(ValueError, match="immutable"):
        await postgres_store.save_result("rrn_1", {"a": 2}, {"e": 2}, "hash2")
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None and got.content_hash == "hash1"
    assert await postgres_store.get_result("rrn_1") == {"a": 1}
    assert await postgres_store.get_evidence("rrn_1") == {"e": 1}
    assert await postgres_store.get_result("rrn_none") is None
    assert await postgres_store.get_evidence("rrn_none") is None


async def test_results_cannot_be_changed_through_the_database_either(
    postgres_store: PostgresStore, research_database: URL
) -> None:
    await postgres_store.create_run(row())
    await postgres_store.save_result("rrn_1", {"a": 1}, {}, "hash1")
    with psycopg.connect(_dsn(research_database), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE rsch_results SET research = '{}'")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM rsch_results")
    assert await postgres_store.get_result("rrn_1") == {"a": 1}


async def test_save_result_for_an_unknown_run_is_not_found(postgres_store: PostgresStore) -> None:
    with pytest.raises(NotFound):
        await postgres_store.save_result("rrn_missing", {}, {}, "h")
    assert await postgres_store.get_result("rrn_missing") is None


async def test_events_round_trip_exactly(postgres_store: PostgresStore) -> None:
    await postgres_store.create_run(row())
    sent = [event("rrn_1", i) for i in range(1, 4)]
    for e in sent:
        await postgres_store.append_event(e)
    got = await postgres_store.events_after("rrn_1", 0)
    assert got == sent
    assert got[0].ts == sent[0].ts and got[0].ts.tzinfo is not None
    assert got[0].schema_ == "research.event.v1"


async def test_the_first_event_must_have_seq_one_and_a_gap_is_refused(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    with pytest.raises(ValueError, match="contiguous"):
        await postgres_store.append_event(event("rrn_1", 2))
    await postgres_store.append_event(event("rrn_1", 1))
    with pytest.raises(ValueError, match="contiguous"):
        await postgres_store.append_event(event("rrn_1", 1))  # a repeat is refused too
    with pytest.raises(ValueError, match="contiguous"):
        await postgres_store.append_event(event("rrn_1", 3))
    assert await postgres_store.last_seq("rrn_1") == 1


async def test_events_and_decisions_of_an_unknown_run(postgres_store: PostgresStore) -> None:
    with pytest.raises(NotFound):
        await postgres_store.append_event(event("rrn_missing", 1))
    with pytest.raises(NotFound):
        await postgres_store.add_decision(
            Decision(run_id="rrn_missing", item_id="RI-1", kind="reject", at=T0)
        )
    assert await postgres_store.last_seq("rrn_missing") == 0
    assert await postgres_store.events_after("rrn_missing", 0) == []


async def test_wait_for_events_returns_at_the_timeout_and_never_hangs(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    started = time.monotonic()
    await postgres_store.wait_for_events("rrn_1", 0, timeout=0.3)
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 2.0
    started = time.monotonic()
    await postgres_store.wait_for_events("rrn_unknown", 0, timeout=0)
    assert time.monotonic() - started < 1.0
    await postgres_store.append_event(event("rrn_1", 1))
    started = time.monotonic()
    await postgres_store.wait_for_events("rrn_1", 0, timeout=5.0)  # already there: immediate
    assert time.monotonic() - started < 1.0


async def test_wait_for_events_wakes_up_within_a_poll_interval(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    await postgres_store.append_event(event("rrn_1", 1))

    async def later() -> None:
        await asyncio.sleep(0.5)
        await postgres_store.append_event(event("rrn_1", 2))

    task = asyncio.create_task(later())
    started = time.monotonic()
    await postgres_store.wait_for_events("rrn_1", 1, timeout=10.0)
    elapsed = time.monotonic() - started
    await task
    assert 0.4 <= elapsed < 3.0
    assert await postgres_store.last_seq("rrn_1") == 2


# --------------------------------------------------------------------------- runs


async def test_every_run_operation_is_tenant_scoped(postgres_store: PostgresStore) -> None:
    await postgres_store.create_run(row("rrn_1", tenant="t1"))
    await postgres_store.save_result("rrn_1", {"a": 1}, {}, "h")
    await postgres_store.update_run("t1", "rrn_1", status="complete")
    assert await postgres_store.get_run("t2", "rrn_1") is None
    assert await postgres_store.active_run("t2", "prog_1") is None
    assert await postgres_store.latest_committed("t2", "prog_1") is None
    with pytest.raises(NotFound):
        await postgres_store.update_run("t2", "rrn_1", status="failed", reason="hijack")
    with pytest.raises(NotFound):
        await postgres_store.update_run("t1", "rrn_missing", status="failed")
    with pytest.raises(NotFound):
        await postgres_store.update_run("t2", "rrn_1")  # no fields: still tenant-checked
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None and got.status == "complete" and got.reason is None


async def test_update_run_refuses_unknown_fields(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    with pytest.raises(ValueError, match="unknown"):
        await postgres_store.update_run("t1", "rrn_1", colour="red")
    await postgres_store.update_run("t1", "rrn_1")  # nothing to change: fine for a run that exists


async def test_update_run_cannot_reactivate_a_run_beside_another_active_one(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row("rrn_1", status="interrupted"))
    await postgres_store.create_run(row("rrn_2", at=5))
    with pytest.raises(RunActive) as err:
        await postgres_store.update_run("t1", "rrn_1", status="running")
    assert err.value.run_id == "rrn_2"
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None and got.status == "interrupted"


async def test_a_run_round_trips_every_field_in_utc(postgres_store: PostgresStore) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    created = datetime(2026, 11, 11, 17, 30, tzinfo=ist)
    full = row("rrn_1").model_copy(
        update={
            "created_at": created,
            "brief_version": 7,
            "export_sha256": "e" * 64,
            "input_json": {"slim": {"features": ["a", "b"], "n": 3}},
        }
    )
    await postgres_store.create_run(full)
    await postgres_store.update_run(
        "t1",
        "rrn_1",
        status="complete",
        started_at=datetime(2026, 11, 11, 12, 1),  # noqa: DTZ001  (naive on purpose: taken as UTC)
        finished_at=T0 + timedelta(minutes=3),
        result_status="partial",
        load_class="standard",
        load_score=31,
        plan_source="llm",
        reason="two targets not found",
        skip_requested=True,
    )
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None
    assert got.created_at == created and got.created_at.utcoffset() == timedelta(0)
    assert got.started_at == datetime(2026, 11, 11, 12, 1, tzinfo=UTC)
    assert got.started_at is not None and got.started_at.utcoffset() == timedelta(0)
    assert got.finished_at == T0 + timedelta(minutes=3)
    assert got.finished_at is not None and got.finished_at.tzinfo is not None
    assert (got.brief_version, got.export_sha256, got.brief_hash) == (7, "e" * 64, "h" * 64)
    assert got.input_json == {"slim": {"features": ["a", "b"], "n": 3}}
    assert (got.result_status, got.load_class, got.load_score, got.plan_source) == (
        "partial",
        "standard",
        31,
        "llm",
    )
    assert got.reason == "two targets not found" and got.skip_requested is True


async def test_latest_committed_orders_by_finish_then_creation_then_id(
    postgres_store: PostgresStore,
) -> None:
    for i, finished in enumerate((10, 30, 20)):
        rid = f"rrn_{i}"
        await postgres_store.create_run(row(rid, at=i))
        await postgres_store.update_run(
            "t1", rid, status="complete", finished_at=T0 + timedelta(minutes=finished)
        )
        await postgres_store.save_result(rid, {"n": i}, {}, f"h{i}")
    latest = await postgres_store.latest_committed("t1", "prog_1")
    assert latest is not None and latest.run_id == "rrn_1"  # finished last
    # a finished run WITHOUT a saved result never counts
    await postgres_store.create_run(row("rrn_nores", at=50))
    await postgres_store.update_run(
        "t1", "rrn_nores", status="failed", finished_at=T0 + timedelta(minutes=99)
    )
    latest = await postgres_store.latest_committed("t1", "prog_1")
    assert latest is not None and latest.run_id == "rrn_1"
    # same finish time: the later created wins, then the larger id
    await postgres_store.create_run(row("rrn_9", at=70))
    await postgres_store.update_run(
        "t1", "rrn_9", status="complete", finished_at=T0 + timedelta(minutes=30)
    )
    await postgres_store.save_result("rrn_9", {}, {}, "h9")
    latest = await postgres_store.latest_committed("t1", "prog_1")
    assert latest is not None and latest.run_id == "rrn_9"


async def test_mark_interrupted_covers_every_active_status_in_every_tenant(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row("rrn_q", program="p1"))
    await postgres_store.create_run(row("rrn_r", program="p2", status="running"))
    await postgres_store.create_run(row("rrn_c", program="p3", status="committing", tenant="t2"))
    await postgres_store.create_run(row("rrn_done", program="p4", status="complete"))
    assert await postgres_store.mark_interrupted() == 3
    assert await postgres_store.mark_interrupted() == 0
    for tenant, rid, status in (
        ("t1", "rrn_q", "interrupted"),
        ("t1", "rrn_r", "interrupted"),
        ("t2", "rrn_c", "interrupted"),
        ("t1", "rrn_done", "complete"),
    ):
        got = await postgres_store.get_run(tenant, rid)
        assert got is not None and got.status == status
        if status == "interrupted":
            assert got.reason == "server restarted"
    assert await postgres_store.active_run("t1", "p1") is None
    await postgres_store.create_run(row("rrn_again", program="p1", at=9))  # the slot is free again


# --------------------------------------------------------------------------- decisions


async def test_decisions_keep_their_order_and_round_trip(postgres_store: PostgresStore) -> None:
    await postgres_store.create_run(row("rrn_1"))
    await postgres_store.create_run(row("rrn_2", program="prog_2"))
    for i, rid in enumerate(("rrn_1", "rrn_2", "rrn_1", "rrn_1")):
        await postgres_store.add_decision(
            Decision(
                run_id=rid,
                item_id=f"RI-{i}",
                kind="reject",
                reason=f"because {i}",
                at=datetime(2026, 11, 11, 12, i, tzinfo=timezone(timedelta(hours=-3))),
            )
        )
    got = await postgres_store.list_decisions("rrn_1")
    assert [d.item_id for d in got] == ["RI-0", "RI-2", "RI-3"]
    assert got[0] == Decision(
        run_id="rrn_1",
        item_id="RI-0",
        kind="reject",
        reason="because 0",
        at=datetime(2026, 11, 11, 15, 0, tzinfo=UTC),
    )
    assert got[0].at.utcoffset() == timedelta(0)
    assert [d.item_id for d in await postgres_store.list_decisions("rrn_2")] == ["RI-1"]


# --------------------------------------------------------------------------- hygiene


async def test_nul_characters_are_removed_instead_of_failing_the_write(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(
        row("rrn_1").model_copy(update={"input_json": {"k\x00ey": "va\x00lue"}})
    )
    await postgres_store.update_run("t1", "rrn_1", reason="bad\x00reason")
    e = event("rrn_1", 1).model_copy(update={"label": "la\x00bel"})
    await postgres_store.append_event(e)
    await postgres_store.save_result("rrn_1", {"quote": "a\x00b"}, {"pages": ["x\x00y"]}, "h")
    await postgres_store.add_decision(
        Decision(run_id="rrn_1", item_id="RI\x00-1", kind="reject", reason="r\x00", at=T0)
    )
    got = await postgres_store.get_run("t1", "rrn_1")
    assert got is not None and got.reason == "badreason"
    assert got.input_json == {"key": "value"}
    assert (await postgres_store.events_after("rrn_1", 0))[0].label == "label"
    assert await postgres_store.get_result("rrn_1") == {"quote": "ab"}
    assert await postgres_store.get_evidence("rrn_1") == {"pages": ["xy"]}
    assert (await postgres_store.list_decisions("rrn_1"))[0].item_id == "RI-1"


async def test_the_password_never_appears_in_repr_or_errors(research_database: URL) -> None:
    secret_url = research_database.set(password="s3cr3t-pw")
    engine = create_store_engine(secret_url)
    try:
        store = PostgresStore(engine)
        for text in (repr(store), str(store), repr(engine), str(engine.url)):
            assert "s3cr3t-pw" not in text
        assert research_database.database is not None
        assert "PostgresStore(" in repr(store) and research_database.database in repr(store)
    finally:
        await engine.dispose()
    with pytest.raises(ValueError) as err:
        normalize_url("sqlite+aiosqlite://user:s3cr3t-pw@host/db")
    assert "s3cr3t-pw" not in str(err.value)
    with pytest.raises(ValueError) as err2:
        create_store_engine("postgresql+asyncpg://user:s3cr3t-pw@host/db")
    assert "s3cr3t-pw" not in str(err2.value)


async def test_storage_timestamps_come_from_the_injected_clock(
    research_engine: AsyncEngine, research_database: URL
) -> None:
    clock = FakeClock(datetime(2026, 11, 12, 8, 0, tzinfo=UTC))
    store = PostgresStore(research_engine, clock=clock)
    await store.create_run(row())
    await store.append_event(event("rrn_1", 1))
    await store.save_result("rrn_1", {}, {}, "h")
    with psycopg.connect(_dsn(research_database)) as conn:
        (event_at,) = conn.execute("SELECT created_at FROM rsch_events").fetchone() or (None,)
        (result_at,) = conn.execute("SELECT created_at FROM rsch_results").fetchone() or (None,)
    assert event_at == result_at == datetime(2026, 11, 12, 8, 0, tzinfo=UTC)


async def test_aclose_disposes_the_pool_and_the_store_still_works_afterwards(
    postgres_store: PostgresStore,
) -> None:
    await postgres_store.create_run(row())
    await postgres_store.aclose()
    await postgres_store.aclose()  # twice is harmless
    got = await postgres_store.get_run("t1", "rrn_1")  # a disposed pool reconnects on demand
    assert got is not None
