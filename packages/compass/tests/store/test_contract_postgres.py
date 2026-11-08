"""The shared store contract suite against the real PostgresStore (ephemeral database), plus the
Postgres-only guarantees: no leaked connections, the derived projections, the database constraints."""

from __future__ import annotations

import asyncio
from typing import LiteralString

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_compass.store import testing as f
from mde_compass.store.contract import CHECKS
from mde_compass.store.ports import Store
from mde_compass.store.postgres import PostgresStore
from mde_compass.store.testing import truncate_all
from mde_core.clock import FakeClock
from mde_core.errors import Conflict, NotFound

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_contract(
    name: str, postgres_store: PostgresStore, compass_engine: AsyncEngine
) -> None:
    async def factory() -> Store:
        return postgres_store

    await CHECKS[name](factory)
    assert compass_engine.pool.checkedout() == 0  # type: ignore[attr-defined]  # no connection leaked


async def test_projections_are_rewritten_from_the_saved_brief(
    postgres_store: PostgresStore, compass_database: URL
) -> None:
    from mde_compass.domain.brief import AssumptionRecord, GlossaryRecord
    from mde_compass.domain.research import ResearchRequest

    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        await uow.sessions.create(rec)
        v1 = f.make_brief(1)
        v1.glossary.append(GlossaryRecord(term="thali", synonyms=["meal plate"]))
        v1.assumptions.append(
            AssumptionRecord(id="ASM-001", claim="Pune first", impact="high", theme="Business")
        )
        v1.research_requests.append(
            ResearchRequest(
                id="RR-001",
                topic="competitors",
                question="Who delivers food in Pune?",
                decision_unlocked="pricing",
                entity="food delivery",
                geography="Pune, India",
                terms=["food delivery"],
            )
        )
        await uow.briefs.save_version(rec.id, v1)
        v2 = f.make_brief(2, summary="changed")
        await uow.briefs.save_version(rec.id, v2)

    dsn = compass_database.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn) as conn:

        def rows(sql: LiteralString) -> list[tuple[object, ...]]:
            return conn.execute(sql, (rec.id,)).fetchall()

        # v2 replaced everything v1 projected: no glossary, assumptions or requests any more
        assert rows("SELECT 1 FROM compass_glossary WHERE session_id = %s") == []
        assert rows("SELECT 1 FROM compass_assumptions WHERE session_id = %s") == []
        assert rows("SELECT 1 FROM compass_research_requests WHERE session_id = %s") == []
        slots = rows(
            "SELECT slot_id, tier, status, value, brief_version FROM compass_slots WHERE session_id = %s"
        )
        assert slots == [("idea.summary", "Locked", "confirmed", "changed", 2)]
        features = rows(
            "SELECT feature_id, scope_status, brief_version FROM compass_features WHERE session_id = %s"
        )
        assert features == [("FTR-001", "in", 2)]

    async with postgres_store.begin(f.TENANT_A) as uow:
        v3 = f.make_brief(3)
        v3.glossary.append(GlossaryRecord(term="thali"))
        v3.glossary.append(GlossaryRecord(term="thali"))  # duplicate terms are fine (position key)
        await uow.briefs.save_version(rec.id, v3)
    with psycopg.connect(dsn) as conn:
        found = conn.execute(
            "SELECT position, term, brief_version FROM compass_glossary WHERE session_id = %s ORDER BY position",
            (rec.id,),
        ).fetchall()
        assert found == [(0, "thali", 3), (1, "thali", 3)]


async def test_database_refuses_changes_to_a_saved_brief_version(
    postgres_store: PostgresStore, compass_engine: AsyncEngine
) -> None:
    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        await uow.sessions.create(rec)
        await uow.briefs.save_version(rec.id, f.make_brief(1))
        await uow.briefs.mark_baseline(rec.id, 1)
    for statement in (
        "UPDATE compass_brief_versions SET hash = 'x' WHERE session_id = :sid",
        "UPDATE compass_brief_versions SET brief = '{}'::jsonb WHERE session_id = :sid",
        "UPDATE compass_brief_versions SET baseline_label = 'B-G2' WHERE session_id = :sid",
        "UPDATE compass_brief_versions SET baseline_label = NULL WHERE session_id = :sid",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with compass_engine.begin() as conn:
                await conn.execute(text(statement), {"sid": rec.id})


async def test_database_enforces_event_uniqueness_and_tenant_consistency(
    postgres_store: PostgresStore, compass_engine: AsyncEngine
) -> None:
    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        await uow.sessions.create(rec)
        await uow.events.record_inbound(rec.id, "e1", "user_message", {"text": "hi"})
        await uow.events.append_outbound(rec.id, "notice", {"code": "x"})
    insert = (
        "INSERT INTO compass_events (session_id, idx, tenant_id, direction, event_id, type, seq, payload, at) "
        "VALUES (:sid, :idx, :tenant, :direction, :event_id, 'x', :seq, '{}'::jsonb, now())"
    )
    cases = [
        {
            "idx": 50,
            "tenant": f.TENANT_A,
            "direction": "in",
            "event_id": "e1",
            "seq": None,
        },  # duplicate id
        {
            "idx": 51,
            "tenant": f.TENANT_A,
            "direction": "out",
            "event_id": None,
            "seq": 1,
        },  # duplicate seq
        {
            "idx": 52,
            "tenant": f.TENANT_A,
            "direction": "out",
            "event_id": None,
            "seq": None,
        },  # out needs seq
        {
            "idx": 53,
            "tenant": f.TENANT_A,
            "direction": "in",
            "event_id": None,
            "seq": None,
        },  # in needs id
        {
            "idx": 54,
            "tenant": f.TENANT_B,
            "direction": "in",
            "event_id": "z",
            "seq": None,
        },  # tenant mismatch
    ]
    for case in cases:
        with pytest.raises(DBAPIError):
            async with compass_engine.begin() as conn:
                await conn.execute(text(insert), {"sid": rec.id, **case})
    # an active turn must say when it started
    with pytest.raises(DBAPIError):
        async with compass_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE compass_sessions SET active_turn = true, turn_started_at = NULL WHERE id = :sid"
                ),
                {"sid": rec.id},
            )


async def test_deleting_a_session_cascades_to_every_table(
    postgres_store: PostgresStore, compass_engine: AsyncEngine
) -> None:
    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        await uow.sessions.create(rec)
        await uow.events.record_inbound(rec.id, "e1", "user_message", {"text": "hi"})
        await uow.briefs.save_version(rec.id, f.make_brief(1))
        await uow.questions.record_asked(rec.id, f.make_card(), {})
        await uow.sources.save_source(rec.id, f.make_source(), chunks=f.make_chunks())
        await uow.sources.save_claims(rec.id, [f.make_claim()])
        await uow.change_sets.save(rec.id, f.make_change_set())
        await uow.call_log.record(rec.id, f.make_call())
        await uow.contributors.upsert(rec.id, f.make_contributor())
    async with compass_engine.begin() as conn:
        await conn.execute(text("DELETE FROM compass_sessions WHERE id = :sid"), {"sid": rec.id})
        for table in (
            "compass_events",
            "compass_brief_versions",
            "compass_slots",
            "compass_features",
            "compass_questions",
            "compass_sources",
            "compass_chunks",
            "compass_claims",
            "compass_change_sets",
            "compass_call_log",
            "compass_contributors",
        ):
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table


async def test_a_conflict_does_not_abort_the_transaction(postgres_store: PostgresStore) -> None:
    """Constraint violations are raised by the repository (ON CONFLICT), never by the database, so
    the unit of work keeps working afterwards."""
    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        await uow.sessions.create(rec)
        with pytest.raises(Conflict):
            await uow.sessions.create(rec)
        await uow.change_sets.save(rec.id, f.make_change_set())
        with pytest.raises(Conflict):
            await uow.change_sets.save(rec.id, f.make_change_set())
        with pytest.raises(NotFound):
            await uow.briefs.get(rec.id)
        await uow.sessions.add_spend(rec.id, 5)  # still a healthy transaction
    async with postgres_store.begin(f.TENANT_A) as uow:
        assert (await uow.sessions.get(rec.id)).spent_micros == 5


async def test_turn_claim_is_exclusive_across_independent_engines(
    compass_database: URL, fake_clock: FakeClock
) -> None:
    """Two pools stand in for two worker processes: still exactly one winner."""
    from mde_compass.store.postgres import create_store_engine

    truncate_all(compass_database)
    first, second = create_store_engine(compass_database), create_store_engine(compass_database)
    try:
        stores = [PostgresStore(first, clock=fake_clock), PostgresStore(second, clock=fake_clock)]
        rec = f.make_session()
        async with stores[0].begin(f.TENANT_A) as uow:
            await uow.sessions.create(rec)

        async def claim(store: PostgresStore) -> bool:
            async with store.begin(f.TENANT_A) as uow:
                return await uow.sessions.try_begin_turn(rec.id)

        results = await asyncio.gather(*(claim(stores[i % 2]) for i in range(8)))
        assert results.count(True) == 1 and results.count(False) == 7
    finally:
        await first.dispose()
        await second.dispose()
    assert first.pool.checkedout() == 0 and second.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_postgres_store_exposes_its_clock_and_engine(
    postgres_store: PostgresStore, compass_engine: AsyncEngine, fake_clock: FakeClock
) -> None:
    assert postgres_store.engine is compass_engine and postgres_store.clock is fake_clock
    rec = f.make_session()
    async with postgres_store.begin(f.TENANT_A) as uow:
        created = await uow.sessions.create(rec)
    assert created.created_at == fake_clock.now()  # every timestamp comes from the injected clock
    assert created.created_at is not None and created.created_at.utcoffset() is not None
