"""Repository behavior beyond the shared contract: JSONB fidelity, projections, UTC, NUL handling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_contracts.brief import AssumedItem
from mde_contracts.cards import CardInstance
from mde_contracts.engines import DiscoveryState, UploadMeta
from mde_contracts.events import StateChanged, UiAction
from mde_contracts.slots import SlotRecord
from mde_core.clock import FakeClock
from mde_core.errors import Conflict, NotFound, ValidationFailed
from mde_persistence import testing_factories as f
from mde_persistence.store import PostgresStore

pytestmark = pytest.mark.integration

A, B = f.TENANT_A, f.TENANT_B
StoreMaker = Callable[..., PostgresStore]


async def _rows(engine: AsyncEngine, sql: str, **params: object) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


# --------------------------------------------------------------------------- sessions


async def test_discovery_state_round_trips_through_jsonb(store: PostgresStore) -> None:
    slots = {
        "idea.summary": SlotRecord(
            slot_id="idea.summary",
            value="मुझे एक ऐप चाहिए café",
            state="volunteered",
            source="text",
            evidence="said so",
            updated_at=datetime(2026, 11, 8, 9, 30, tzinfo=UTC),
        ),
        "scope.features": SlotRecord(
            slot_id="scope.features", value=["booking", "payments"], state="confirmed", source="tap"
        ),
        "behavior.offline": SlotRecord(slot_id="behavior.offline", value=False, state="assumed"),
        "a11y": SlotRecord(slot_id="a11y", value=0, state="assumed", source="default"),
        "design.accent": SlotRecord(slot_id="design.accent", value=0.25, state="assumed"),
        "brand.name": SlotRecord(slot_id="brand.name", value=None, state="empty"),
    }
    discovery = DiscoveryState(
        slots=slots,
        summary="Founder wants a clinic booking app.",
        pending_card=f.new_card_instance(),
        cards_asked=["Q-look-01", "Q-look-01:2"],
        card_count=2,
        turn_count=5,
        language="hi",
        uploads=[UploadMeta(upload_id="upl_1", filename="prd.md", media_type="text/markdown")],
        visual_rounds={"Q-look-01": 2},
        extra={"k": "v"},
    )
    rec = f.new_session(
        discovery=discovery,
        queued_messages=["also add chat", "and dark mode"],
        title="Clinic app",
        state="PLANNING",
        plan_phase="research",
        origin="BRIEF_REVIEW",
        paused_from=None,
        brief_version=2,
        approved_brief_version=2,
        research_version=1,
        spec_version=3,
        handed_off_version=None,
        mode="enterprise",
        policy_id="acme",
        language="hi",
    )
    async with store.begin(A) as uow:
        created = await uow.sessions.create(rec)
    async with store.begin(A) as uow:
        got = await uow.sessions.get(rec.id)
    assert got is not None and got == created
    assert got.discovery == discovery
    assert isinstance(got.discovery.pending_card, CardInstance)
    for field in ("queued_messages", "state", "plan_phase", "origin", "brief_version", "mode"):
        assert getattr(got, field) == getattr(rec, field)


async def test_timestamps_are_utc_and_updated_at_moves(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    rec = f.new_session()
    async with store.begin(A) as uow:
        created = await uow.sessions.create(rec)
    assert created.created_at == fake_clock.now() == created.updated_at
    assert created.created_at is not None and created.created_at.utcoffset() == timedelta(0)
    fake_clock.advance(60)
    async with store.begin(A) as uow:
        saved = await uow.sessions.save(created, expected_version=0)
    assert saved.created_at == created.created_at, "created_at never changes"
    assert saved.updated_at == fake_clock.now() and saved.updated_at != created.updated_at


async def test_list_for_owner_is_most_recently_updated_first(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    ids = []
    for _ in range(3):
        async with store.begin(A) as uow:
            ids.append((await uow.sessions.create(f.new_session(owner_id="usr_1"))).id)
        fake_clock.advance(10)
    async with store.begin(A) as uow:  # touch the oldest: it moves to the front
        oldest = await uow.sessions.get(ids[0])
        assert oldest is not None
        await uow.sessions.save(oldest, expected_version=0)
        assert [s.id for s in await uow.sessions.list_for_owner("usr_1")] == [
            ids[0],
            ids[2],
            ids[1],
        ]


async def test_tenants_are_created_implicitly_and_sessions_stay_apart(
    store: PostgresStore, pg_engine: AsyncEngine
) -> None:
    async with store.begin(A) as uow:
        await uow.sessions.create(f.new_session(tenant_id=A, owner_id="same_user"))
    async with store.begin(B) as uow:
        await uow.sessions.create(f.new_session(tenant_id=B, owner_id="same_user"))
        assert len(await uow.sessions.list_for_owner("same_user")) == 1
    rows = await _rows(pg_engine, "SELECT id FROM tenant ORDER BY id")
    assert [r["id"] for r in rows] == [A, B]


async def test_slot_projection_follows_the_session(
    store: PostgresStore, pg_engine: AsyncEngine
) -> None:
    rec = f.new_session(
        slots={
            "idea.summary": SlotRecord(
                slot_id="idea.summary", value="A clinic app", state="volunteered", source="text"
            ),
            "scope.features": SlotRecord(
                slot_id="scope.features", value=["a", "b"], state="assumed", source="default"
            ),
            "brand.name": SlotRecord(slot_id="brand.name"),
        }
    )
    async with store.begin(A) as uow:
        created = await uow.sessions.create(rec)
    rows = await _rows(
        pg_engine, "SELECT * FROM slot WHERE session_id = :s ORDER BY slot_id", s=rec.id
    )
    assert [r["slot_id"] for r in rows] == ["brand.name", "idea.summary", "scope.features"]
    by_id = {r["slot_id"]: r for r in rows}
    assert by_id["brand.name"]["value"] is None  # SQL NULL, not JSON null
    assert by_id["idea.summary"]["value"] == "A clinic app"
    assert (
        by_id["scope.features"]["value"] == ["a", "b"]
        and by_id["scope.features"]["state"] == "assumed"
    )
    assert {r["tenant_id"] for r in rows} == {A}

    updated = created.model_copy(
        update={
            "discovery": DiscoveryState(
                slots={
                    "idea.summary": SlotRecord(
                        slot_id="idea.summary",
                        value="A dental app",
                        state="confirmed",
                        source="tap",
                    ),
                    "auth.method": SlotRecord(
                        slot_id="auth.method", value="phone", state="confirmed", source="tap"
                    ),
                }
            )
        }
    )
    async with store.begin(A) as uow:
        await uow.sessions.save(updated, expected_version=0)
    rows = await _rows(
        pg_engine, "SELECT * FROM slot WHERE session_id = :s ORDER BY slot_id", s=rec.id
    )
    assert [r["slot_id"] for r in rows] == ["auth.method", "idea.summary"]
    assert {r["slot_id"]: (r["value"], r["state"]) for r in rows} == {
        "auth.method": ("phone", "confirmed"),
        "idea.summary": ("A dental app", "confirmed"),
    }
    async with store.begin(A) as uow:  # no slots at all: the projection empties
        again = await uow.sessions.get(rec.id)
        assert again is not None
        await uow.sessions.save(
            again.model_copy(update={"discovery": DiscoveryState()}), expected_version=1
        )
    assert await _rows(pg_engine, "SELECT 1 FROM slot WHERE session_id = :s", s=rec.id) == []


async def test_nul_characters_never_reach_the_database(store: PostgresStore) -> None:
    rec = f.new_session(title="bad\x00title", queued_messages=["a\x00b"])
    async with store.begin(A) as uow:
        created = await uow.sessions.create(rec)
        assert created.title == "badtitle" and created.queued_messages == ["ab"]
        event = f.user_message("hello\x00world")
        assert await uow.events.record_inbound(rec.id, event) is True
        stored = await uow.events.append_outbound(
            rec.id, f.assistant_message(rec.id, text="x\x00y")
        )
        assert stored.type == "assistant_message" and stored.text == "x\x00y"  # the caller's object
        listed = await uow.events.list_outbound(rec.id)
        assert listed[0].type == "assistant_message" and listed[0].text == "xy"


# --------------------------------------------------------------------------- events


async def test_card_instances_are_projected_and_answers_recorded(
    store: PostgresStore, pg_engine: AsyncEngine
) -> None:
    ses = f.new_session()
    shown = f.new_card_instance(card_id="Q-look-01", slot="design.direction")
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        card_out = await uow.events.append_outbound(ses.id, f.card_event(ses.id, instance=shown))
    rows = await _rows(pg_engine, "SELECT * FROM card_instance WHERE session_id = :s", s=ses.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == shown.instance_id and row["card_id"] == "Q-look-01"
    assert row["slot"] == "design.direction" and row["card_type"] == "choice3"
    assert row["status"] == "shown" and row["seq"] == card_out.seq and row["answer"] is None
    assert row["card"]["card"]["options"][0]["id"] == "clean"

    answer = f.option_selected(shown.instance_id, option_ids=["bold"])
    async with store.begin(A) as uow:
        assert await uow.events.record_inbound(ses.id, answer) is True
        assert await uow.events.record_inbound(ses.id, answer) is False  # duplicate: not re-applied
    row = (await _rows(pg_engine, "SELECT * FROM card_instance WHERE session_id = :s", s=ses.id))[0]
    assert row["status"] == "answered" and row["answered_event_id"] == answer.event_id
    assert row["answer"]["option_ids"] == ["bold"] and row["answered_at"] is not None

    skip = f.option_selected(shown.instance_id, option_ids=[], skipped=True)
    decide = f.option_selected(shown.instance_id, option_ids=[], you_decide=True)
    other = f.option_selected("crd_NOSUCHCARD")  # an answer for a card we never projected
    async with store.begin(A) as uow:
        await uow.events.record_inbound(ses.id, skip)
    assert (await _statuses(pg_engine, ses.id)) == ["skipped"]
    async with store.begin(A) as uow:
        await uow.events.record_inbound(ses.id, decide)
        await uow.events.record_inbound(ses.id, other)
        await uow.events.record_inbound(ses.id, UiAction(event_id="evt_UIACTION1", action="stop"))
    assert (await _statuses(pg_engine, ses.id)) == ["you_decide"]


async def _statuses(engine: AsyncEngine, session_id: str) -> list[str]:
    async with engine.connect() as conn:  # a second connection: only committed data is visible
        rows = await conn.execute(
            text("SELECT status FROM card_instance WHERE session_id = :s"), {"s": session_id}
        )
        return [r[0] for r in rows]


async def test_events_must_belong_to_the_session_and_be_unique(store: PostgresStore) -> None:
    one, two = f.new_session(), f.new_session()
    async with store.begin(A) as uow:
        await uow.sessions.create(one)
        await uow.sessions.create(two)
        with pytest.raises(ValidationFailed):
            await uow.events.append_outbound(one.id, f.assistant_message(two.id))
        sent = await uow.events.append_outbound(one.id, f.assistant_message(one.id))
        with pytest.raises(Conflict):
            await uow.events.append_outbound(one.id, sent)  # the same event id twice
        # the unit of work is still healthy and the sequence keeps counting
        nxt = await uow.events.append_outbound(one.id, f.assistant_message(one.id))
        assert nxt.seq > sent.seq


async def test_outbound_seq_is_per_session_and_independent_of_the_cas_token(
    store: PostgresStore,
) -> None:
    one, two = f.new_session(), f.new_session()
    async with store.begin(A) as uow:
        created = await uow.sessions.create(one)
        await uow.sessions.create(two)
        s1 = [
            await uow.events.append_outbound(one.id, f.assistant_message(one.id)) for _ in range(3)
        ]
        s2 = await uow.events.append_outbound(two.id, f.assistant_message(two.id))
        assert [e.seq for e in s1] == [1, 2, 3] and s2.seq == 1
        # appending events does not bump the session version: saves are not spuriously stale
        saved = await uow.sessions.save(created, expected_version=0)
        assert saved.version == 1
    async with store.begin(A) as uow:
        after = await uow.events.append_outbound(one.id, f.assistant_message(one.id))
        assert after.seq == 4


async def test_every_outbound_event_type_round_trips(store: PostgresStore) -> None:
    ses = f.new_session()
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        sent = [
            await uow.events.append_outbound(ses.id, f.assistant_message(ses.id)),
            await uow.events.append_outbound(ses.id, f.card_event(ses.id)),
            await uow.events.append_outbound(
                ses.id,
                StateChanged(
                    event_id="evt_STATE0000001",
                    session_id=ses.id,
                    from_state="DISCOVERING",
                    to_state="BRIEF_REVIEW",
                ),
            ),
        ]
        listed = await uow.events.list_outbound(ses.id)
        assert listed == sent


# --------------------------------------------------------------------------- briefs / research


async def test_brief_status_is_kept_in_the_document_too(store: PostgresStore) -> None:
    ses = f.new_session()
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        await uow.briefs.add(f.new_brief(ses.id, A, version=1))
        added = await uow.briefs.add(
            f.new_brief(ses.id, A, version=2).model_copy(
                update={
                    "brief": f.new_brief(ses.id, A, version=2).brief.model_copy(
                        update={
                            "assumptions": [
                                AssumedItem(
                                    slot_id="auth.method",
                                    label="Login",
                                    value_text="Phone",
                                    reason="Most common",
                                )
                            ]
                        }
                    )
                }
            )
        )
        assert added.brief.assumptions[0].value_text == "Phone"
        await uow.briefs.set_status(ses.id, 1, "approved")
        await uow.briefs.set_status(ses.id, 1, "approved")  # repeating is a no-op
        await uow.briefs.set_status(ses.id, 1, "superseded")
        with pytest.raises(Conflict):
            await uow.briefs.set_status(ses.id, 1, "approved")  # superseded is final
        await uow.briefs.set_status(ses.id, 2, "approved")
        with pytest.raises(Conflict):
            await uow.briefs.set_status(ses.id, 2, "draft")  # approved cannot go back
        with pytest.raises(ValidationFailed):
            await uow.briefs.set_status(ses.id, 2, "frozen")
        got = await uow.briefs.get(ses.id, 1)
        assert got is not None and got.brief.status == "superseded"
        assert got.brief.compute_hash() == got.brief.hash  # status is excluded from the hash


async def test_research_facts_and_jobs_are_projected(
    store: PostgresStore, pg_engine: AsyncEngine
) -> None:
    ses = f.new_session()
    note = f.new_research(ses.id, A, version=1, facts=3)
    bare = f.new_research(ses.id, A, version=2, facts=0)
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        await uow.research.add(note)
        await uow.research.add(bare)
    jobs = await _rows(
        pg_engine, "SELECT * FROM research_job WHERE session_id = :s ORDER BY job", s=ses.id
    )
    assert [(j["note_version"], j["job"], j["fact_count"], j["status"]) for j in jobs] == [
        (1, "R1", 2, "ok"),
        (1, "R2", 1, "ok"),
    ]
    facts = await _rows(
        pg_engine, "SELECT * FROM research_fact WHERE session_id = :s ORDER BY fact_id", s=ses.id
    )
    assert [x["fact_id"] for x in facts] == ["F-001", "F-002", "F-003"]
    first = facts[0]
    assert first["job"] == "R1" and first["confidence"] == "high" and first["verified"] is True
    assert first["primary_url"] == "https://example.com/source-1"
    assert first["retrieved_on"] == date(2026, 11, 8)
    assert first["sources"][0]["domain"] == "example.com" and first["tenant_id"] == A
    async with store.begin(A) as uow:
        latest = await uow.research.latest(ses.id)
        assert latest is not None and latest.note.version == 2 and latest.note.facts == []


# --------------------------------------------------------------------------- specs


async def test_spec_plan_round_trips_and_content_is_frozen(store: PostgresStore) -> None:
    ses = f.new_session()
    rec = f.new_spec(ses.id, A, version=1, with_plan=True)
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        added = await uow.specs.add(rec)
        assert added.plan == rec.plan and added.spec == rec.spec and added.hash == rec.hash
        assert added.created_at is not None and added.approved_at is None
        got = await uow.specs.get(ses.id, 1)
        assert got == added


async def test_set_status_rules_for_stamps_and_idempotence(
    make_store: StoreMaker, fake_clock: FakeClock
) -> None:
    store = make_store(clock=fake_clock)
    ses = f.new_session()
    stamp = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        await uow.specs.add(f.new_spec(ses.id, A, version=1))
        await uow.specs.add(f.new_spec(ses.id, A, version=2))
        first = await uow.specs.set_status(ses.id, 1, "approved")
        assert first.approved_at == fake_clock.now()  # stamped from the clock
        fake_clock.advance(5)
        again = await uow.specs.set_status(ses.id, 1, "approved")  # no-op, nothing moves
        assert again.approved_at == first.approved_at
        with pytest.raises(Conflict):
            await uow.specs.set_status(ses.id, 1, "approved", approved_at=stamp)  # already stamped
        explicit = await uow.specs.set_status(ses.id, 2, "approved", approved_at=stamp)
        assert explicit.approved_at == stamp
        with_sha = await uow.specs.set_status(ses.id, 2, "handed_off", git_sha="deadbeef")
        assert with_sha.git_sha == "deadbeef" and with_sha.status == "handed_off"
        same_sha = await uow.specs.set_status(ses.id, 2, "handed_off", git_sha="deadbeef")
        assert same_sha == with_sha  # idempotent for the same commit
        with pytest.raises(Conflict):
            await uow.specs.set_status(ses.id, 2, "handed_off", git_sha="cafebabe")
        with pytest.raises(ValidationFailed):
            await uow.specs.set_status(ses.id, 1, "frozen")  # type: ignore[arg-type]


async def test_approvals_keep_comments_and_roles(store: PostgresStore) -> None:
    ses = f.new_session()
    async with store.begin(A) as uow:
        await uow.sessions.create(ses)
        await uow.approvals.add(
            f.new_approval(
                ses.id, gate="plan", version=3, hash="ph", role="security", decision="change"
            ).model_copy(update={"comment": "needs SSO\x00", "via": "typed"})
        )
        (got,) = await uow.approvals.list(ses.id, "plan", version=3, hash="ph")
        assert got.role == "security" and got.decision == "change" and got.via == "typed"
        assert got.comment == "needs SSO" and got.at is not None
        assert got.at.tzinfo is not None


async def test_unknown_sessions_are_not_found_for_every_add(store: PostgresStore) -> None:
    async with store.begin(A) as uow:
        with pytest.raises(NotFound):
            await uow.briefs.add(f.new_brief("ses_NOSUCH", A))
        with pytest.raises(NotFound):
            await uow.research.add(f.new_research("ses_NOSUCH", A))
        with pytest.raises(NotFound):
            await uow.specs.add(f.new_spec("ses_NOSUCH", A))
        with pytest.raises(NotFound):
            await uow.approvals.add(f.new_approval("ses_NOSUCH"))
