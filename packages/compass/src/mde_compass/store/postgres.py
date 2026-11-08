"""``PostgresStore``: the ``Store`` / ``UnitOfWork`` implementation over SQLAlchemy Core (prompt 5.8).

``Store.begin(tenant_id)`` opens one transaction on one pooled connection and hands out repositories
bound to that tenant; it commits on clean exit and rolls back on any exception. Adapted from
``mde_persistence.store`` and its repositories. Rules kept:

* Every query filters by ``tenant_id``; a foreign or unknown session id is ``NotFound``.
* Constraint violations never abort the transaction: ``ON CONFLICT ... RETURNING`` (or an explicit
  existence check under the session row lock), and the repository raises ``Conflict`` itself.
* ``try_begin_turn`` takes a transaction-scoped advisory lock on
  ``hashtextextended('mde:compass:turn:<session_id>', 0)`` and then claims the turn with one conditional
  ``UPDATE ... WHERE active_turn = false OR stale ... RETURNING``; the row lock makes it race-safe.
* The outbound ``seq`` comes from ``UPDATE compass_sessions SET outbound_seq = outbound_seq + 1 ...
  RETURNING``: the row lock is held until commit, so ``seq`` order equals commit order.
* Transactions never span a model call (ADR 0004).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import JsonValue
from sqlalchemy import Table, and_, delete, func, literal, null, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mde_compass.domain.brief import DICT_SECTIONS, Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.changes import ChangeSet
from mde_compass.domain.claims import Chunk, Claim, SourceInfo
from mde_compass.domain.events import StoredEvent
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.domain.session import Contributor, SessionRecord, SessionState
from mde_compass.store import models as m
from mde_compass.store.common import (
    DEFAULT_BASELINE_LABEL,
    STALE_TURN,
    clean_json,
    dump_model,
    payload_text,
    turn_lock_key,
)
from mde_compass.store.ports import (
    BriefVersionRecord,
    BriefVersionSummary,
    CallLogEntry,
    HistoryPage,
    QuestionRecord,
)
from mde_core.clock import Clock, SystemClock
from mde_core.errors import Conflict, NotFound, TenantViolation, ValidationFailed
from mde_persistence.db import create_engine
from mde_persistence.jsonutil import utc, utc_required

Outcome = Literal["answered", "skipped", "you_decide", "back"]


def create_store_engine(url: str | URL, *, echo: bool = False) -> AsyncEngine:
    """The async engine for the Compass store (psycopg 3, pool of 5, no overflow, pre-ping)."""
    return create_engine(url=url, echo=echo)


def tbl(model: type[m.Base]) -> Table:
    """The ``Table`` behind a declarative model (Core statements are used for all DML)."""
    return cast(Table, model.__table__)


SESSION = tbl(m.SessionRow)
EVENT = tbl(m.EventRow)
SOURCE = tbl(m.SourceRow)
CHUNK = tbl(m.ChunkRow)
CLAIM = tbl(m.ClaimRow)
QUESTION = tbl(m.QuestionRow)
BRIEF = tbl(m.BriefVersionRow)
SLOT = tbl(m.SlotRow)
FEATURE = tbl(m.FeatureRow)
ASSUMPTION = tbl(m.AssumptionRow)
RESEARCH = tbl(m.ResearchRequestRow)
GLOSSARY = tbl(m.GlossaryRow)
CONTRIBUTOR = tbl(m.ContributorRow)
CHANGE_SET = tbl(m.ChangeSetRow)
CALL_LOG = tbl(m.CallLogRow)


def _page_limit(limit: int) -> int:
    return max(1, limit)


class _Repo:
    """A repository bound to one tenant and one transaction."""

    def __init__(self, session: AsyncSession, tenant_id: str, clock: Clock) -> None:
        self._s = session
        self.tenant = tenant_id
        self._clock = clock

    def own(self, tenant_id: str) -> str:
        if tenant_id != self.tenant:
            raise TenantViolation(
                "record belongs to another tenant than the unit of work is bound to",
                details={"record_tenant": tenant_id, "bound_tenant": self.tenant},
            )
        return self.tenant

    def now(self) -> datetime:
        return self._clock.now()

    async def require_session(self, session_id: str) -> None:
        """``NotFound`` unless the session exists in this tenant (other tenants' ids look missing)."""
        stmt = select(literal(1)).where(
            SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant
        )
        if (await self._s.execute(stmt)).first() is None:
            raise NotFound(f"session {session_id!r} not found")

    async def lock_session(self, session_id: str) -> Any:
        """``SELECT ... FOR NO KEY UPDATE`` of the session row: writers of one session queue up here
        until their transaction ends. ``NotFound`` for unknown or foreign ids. (NO KEY UPDATE, like the
        plain ``UPDATE`` of the counters, does not block child-row inserts, which only take KEY SHARE.)"""
        stmt = (
            select(*SESSION.c)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .with_for_update(key_share=True)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"session {session_id!r} not found")
        return row


# --------------------------------------------------------------------------- sessions


def _session_record(row: Any) -> SessionRecord:
    data: dict[str, Any] = dict(row.payload)
    data.update(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        mode=row.mode,
        locale=row.locale,
        state=row.state,
        row_version=row.row_version,
        outbound_seq=row.outbound_seq,
        active_turn=row.active_turn,
        spent_micros=row.spent_micros,
        llm_calls=row.llm_calls,
        created_at=utc_required(row.created_at),
        updated_at=utc_required(row.updated_at),
    )
    return SessionRecord.model_validate(data)


class PgSessionRepo(_Repo):
    async def create(self, record: SessionRecord) -> SessionRecord:
        tenant = self.own(record.tenant_id)
        now = self.now()
        stmt = (
            pg_insert(SESSION)
            .values(
                id=record.id,
                tenant_id=tenant,
                user_id=record.user_id,
                mode=record.mode,
                locale=record.locale,
                state=record.state,
                row_version=record.row_version,
                outbound_seq=0,
                event_idx=0,
                active_turn=False,
                turn_started_at=None,
                spent_micros=record.spent_micros,
                llm_calls=record.llm_calls,
                payload=dump_model(record),
                created_at=utc(record.created_at) or now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[SESSION.c.id])
            .returning(*SESSION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"session {record.id!r} already exists")
        return _session_record(row)

    async def get(self, session_id: str) -> SessionRecord:
        stmt = select(*SESSION.c).where(
            SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"session {session_id!r} not found")
        return _session_record(row)

    async def list_for_user(
        self, user_id: str, *, include_archived: bool = False, limit: int = 50
    ) -> list[SessionRecord]:
        stmt = select(*SESSION.c).where(
            SESSION.c.tenant_id == self.tenant, SESSION.c.user_id == user_id
        )
        if not include_archived:
            stmt = stmt.where(SESSION.c.state != "ARCHIVED")
        stmt = stmt.order_by(SESSION.c.updated_at.desc(), SESSION.c.id.desc()).limit(
            _page_limit(limit)
        )
        return [_session_record(r) for r in (await self._s.execute(stmt)).all()]

    async def save(self, record: SessionRecord) -> SessionRecord:
        tenant = self.own(record.tenant_id)
        stmt = (
            update(SESSION)
            .where(
                SESSION.c.id == record.id,
                SESSION.c.tenant_id == tenant,
                SESSION.c.row_version == record.row_version,
            )
            .values(
                mode=record.mode,
                locale=record.locale,
                state=record.state,
                payload=dump_model(record),
                row_version=SESSION.c.row_version + 1,
                updated_at=self.now(),
            )
            .returning(*SESSION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            await self.require_session(record.id)  # NotFound for unknown or foreign ids
            raise Conflict(
                f"session {record.id}: stale write (expected row_version {record.row_version})",
                details={"session_id": record.id, "expected_row_version": record.row_version},
            )
        return _session_record(row)

    async def try_begin_turn(self, session_id: str) -> bool:
        # Lock order everywhere is: session row first, advisory lock second (no deadlock between a
        # unit that wrote events and one that claims a turn).
        await self.lock_session(session_id)  # NotFound for unknown or foreign ids
        await self._s.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(turn_lock_key(session_id), 0)))
        )
        now = self.now()
        stmt = (
            update(SESSION)
            .where(
                SESSION.c.id == session_id,
                SESSION.c.tenant_id == self.tenant,
                or_(
                    SESSION.c.active_turn.is_(False),
                    SESSION.c.turn_started_at.is_(None),
                    SESSION.c.turn_started_at < now - STALE_TURN,
                ),
            )
            .values(active_turn=True, turn_started_at=now)
            .returning(SESSION.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def end_turn(self, session_id: str) -> None:
        stmt = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .values(active_turn=False, turn_started_at=None)
            .returning(SESSION.c.id)
        )
        if (await self._s.execute(stmt)).first() is None:
            raise NotFound(f"session {session_id!r} not found")

    async def add_spend(self, session_id: str, cost_micros: int, calls: int = 1) -> int:
        if cost_micros < 0 or calls < 0:
            raise ValidationFailed("spend and call counts cannot be negative")
        stmt = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .values(
                spent_micros=SESSION.c.spent_micros + cost_micros,
                llm_calls=SESSION.c.llm_calls + calls,
            )
            .returning(SESSION.c.spent_micros)
        )
        spent = (await self._s.execute(stmt)).scalar_one_or_none()
        if spent is None:
            raise NotFound(f"session {session_id!r} not found")
        return int(spent)

    async def set_state(self, session_id: str, state: SessionState) -> SessionRecord:
        stmt = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .values(state=state, row_version=SESSION.c.row_version + 1, updated_at=self.now())
            .returning(*SESSION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"session {session_id!r} not found")
        return _session_record(row)


# --------------------------------------------------------------------------- events


def _stored_event(row: Any) -> StoredEvent:
    return StoredEvent(
        idx=row.idx,
        direction=row.direction,
        type=row.type,
        event_id=row.event_id,
        turn_id=row.turn_id,
        seq=row.seq,
        payload=row.payload,
        at=utc(row.at),
    )


class PgEventRepo(_Repo):
    async def record_inbound(
        self, session_id: str, event_id: str, event_type: str, payload: dict[str, JsonValue]
    ) -> bool:
        await self.lock_session(session_id)
        seen = select(literal(1)).where(
            EVENT.c.session_id == session_id,
            EVENT.c.direction == "in",
            EVENT.c.event_id == event_id,
        )
        if (await self._s.execute(seen)).first() is not None:
            return False
        bump = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .values(event_idx=SESSION.c.event_idx + 1)
            .returning(SESSION.c.event_idx)
        )
        idx = int((await self._s.execute(bump)).scalar_one())
        cleaned = clean_json(payload)
        stmt = (
            pg_insert(EVENT)
            .values(
                session_id=session_id,
                idx=idx,
                tenant_id=self.tenant,
                direction="in",
                event_id=event_id,
                type=event_type,
                turn_id=None,
                seq=None,
                payload=cleaned,
                search_text=payload_text(cleaned),
                at=self.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[EVENT.c.session_id, EVENT.c.event_id],
                index_where=text("direction = 'in'"),
            )
            .returning(EVENT.c.idx)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def append_outbound(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        turn_id: str | None = None,
    ) -> int:
        bump = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
            .values(outbound_seq=SESSION.c.outbound_seq + 1, event_idx=SESSION.c.event_idx + 1)
            .returning(SESSION.c.outbound_seq, SESSION.c.event_idx)
        )
        counters = (await self._s.execute(bump)).one_or_none()
        if counters is None:
            raise NotFound(f"session {session_id!r} not found")
        seq, idx = int(counters.outbound_seq), int(counters.event_idx)
        cleaned = clean_json(payload)
        stmt = (
            pg_insert(EVENT)
            .values(
                session_id=session_id,
                idx=idx,
                tenant_id=self.tenant,
                direction="out",
                event_id=None,
                type=event_type,
                turn_id=turn_id,
                seq=seq,
                payload=cleaned,
                search_text=payload_text(cleaned),
                at=self.now(),
            )
            .on_conflict_do_nothing()
            .returning(EVENT.c.idx)
        )
        if (await self._s.execute(stmt)).first() is None:  # unreachable under the row lock
            raise Conflict(f"outbound seq {seq} of session {session_id} was already recorded")
        return seq

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        await self.require_session(session_id)
        stmt = (
            select(*EVENT.c)
            .where(
                EVENT.c.session_id == session_id,
                EVENT.c.tenant_id == self.tenant,
                EVENT.c.direction == "out",
                EVENT.c.seq > after_seq,
            )
            .order_by(EVENT.c.seq)
            .limit(_page_limit(limit))
        )
        return [_stored_event(r) for r in (await self._s.execute(stmt)).all()]

    async def list_history(
        self,
        session_id: str,
        *,
        after_idx: int = 0,
        limit: int = 50,
        q: str | None = None,
        turn_ids: list[str] | None = None,
    ) -> HistoryPage:
        await self.require_session(session_id)
        conditions: list[Any] = [
            EVENT.c.session_id == session_id,
            EVENT.c.tenant_id == self.tenant,
            EVENT.c.idx > after_idx,
        ]
        if q:
            conditions.append(func.strpos(EVENT.c.search_text, q.casefold()) > 0)
        if turn_ids is not None:
            conditions.append(
                or_(
                    and_(EVENT.c.direction == "in", EVENT.c.event_id.in_(turn_ids)),
                    and_(EVENT.c.direction == "out", EVENT.c.turn_id.in_(turn_ids)),
                )
            )
        size = _page_limit(limit)
        stmt = select(*EVENT.c).where(*conditions).order_by(EVENT.c.idx).limit(size + 1)
        rows = (await self._s.execute(stmt)).all()
        items = [_stored_event(r) for r in rows[:size]]
        more = len(rows) > size
        return HistoryPage(items=items, next_after_idx=items[-1].idx if more and items else None)

    async def get_inbound(self, session_id: str, event_id: str) -> StoredEvent | None:
        await self.require_session(session_id)
        stmt = select(*EVENT.c).where(
            EVENT.c.session_id == session_id,
            EVENT.c.tenant_id == self.tenant,
            EVENT.c.direction == "in",
            EVENT.c.event_id == event_id,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _stored_event(row)


# --------------------------------------------------------------------------- briefs


def _version_record(row: Any) -> BriefVersionRecord:
    return BriefVersionRecord(
        session_id=row.session_id,
        version=row.version,
        brief=Brief.model_validate(row.brief),
        hash=row.hash,
        section_hashes=row.section_hashes,
        baseline_label=row.baseline_label,
        change_set_id=row.change_set_id,
        created_at=utc(row.created_at),
    )


class PgBriefRepo(_Repo):
    async def save_version(
        self, session_id: str, brief: Brief, *, change_set_id: str | None = None
    ) -> BriefVersionRecord:
        await self.lock_session(session_id)  # serialises writers: the second sees the first's row
        latest_stmt = select(func.max(BRIEF.c.version)).where(BRIEF.c.session_id == session_id)
        latest = (await self._s.execute(latest_stmt)).scalar_one() or 0
        if brief.version != latest + 1:
            raise Conflict(
                f"brief version {brief.version} is not the next version (latest saved: {latest})",
                details={"session_id": session_id, "latest": latest, "got": brief.version},
            )
        stmt = (
            pg_insert(BRIEF)
            .values(
                session_id=session_id,
                version=brief.version,
                tenant_id=self.tenant,
                brief=dump_model(brief),
                hash=brief_hash(brief),
                section_hashes=section_hashes(brief),
                baseline_label=None,
                change_set_id=change_set_id,
                created_at=self.now(),
            )
            .on_conflict_do_nothing(index_elements=[BRIEF.c.session_id, BRIEF.c.version])
            .returning(*BRIEF.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:  # unreachable under the row lock; the primary key is the backstop
            raise Conflict(f"brief version {brief.version} already exists")
        await self._project(session_id, brief)
        return _version_record(row)

    async def get(self, session_id: str, version: int | None = None) -> BriefVersionRecord:
        await self.require_session(session_id)
        stmt = select(*BRIEF.c).where(
            BRIEF.c.session_id == session_id, BRIEF.c.tenant_id == self.tenant
        )
        if version is None:
            stmt = stmt.order_by(BRIEF.c.version.desc()).limit(1)
        else:
            stmt = stmt.where(BRIEF.c.version == version)
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            what = "has no brief yet" if version is None else f"has no brief version {version}"
            raise NotFound(f"session {session_id} {what}")
        return _version_record(row)

    async def list_versions(self, session_id: str) -> list[BriefVersionSummary]:
        await self.require_session(session_id)
        stmt = (
            select(
                BRIEF.c.version,
                BRIEF.c.hash,
                BRIEF.c.baseline_label,
                BRIEF.c.change_set_id,
                BRIEF.c.created_at,
            )
            .where(BRIEF.c.session_id == session_id, BRIEF.c.tenant_id == self.tenant)
            .order_by(BRIEF.c.version)
        )
        return [
            BriefVersionSummary(
                version=r.version,
                hash=r.hash,
                baseline_label=r.baseline_label,
                change_set_id=r.change_set_id,
                created_at=utc(r.created_at),
            )
            for r in (await self._s.execute(stmt)).all()
        ]

    async def mark_baseline(
        self, session_id: str, version: int, label: str = DEFAULT_BASELINE_LABEL
    ) -> BriefVersionRecord:
        await self.lock_session(session_id)
        target = await self.get(session_id, version)
        if target.baseline_label == label:
            return target
        if target.baseline_label is not None:
            raise Conflict(
                f"brief version {version} is already the baseline {target.baseline_label!r}"
            )
        taken = select(BRIEF.c.version).where(
            BRIEF.c.session_id == session_id, BRIEF.c.baseline_label == label
        )
        holder = (await self._s.execute(taken)).scalar_one_or_none()
        if holder is not None:
            raise Conflict(f"baseline {label!r} already labels version {holder} (immutable)")
        stmt = (
            update(BRIEF)
            .where(
                BRIEF.c.session_id == session_id,
                BRIEF.c.version == version,
                BRIEF.c.baseline_label.is_(None),
            )
            .values(baseline_label=label)
            .returning(*BRIEF.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:  # unreachable under the row lock
            raise Conflict(f"brief version {version} could not be labelled {label!r}")
        return _version_record(row)

    async def get_baseline(
        self, session_id: str, label: str = DEFAULT_BASELINE_LABEL
    ) -> BriefVersionRecord | None:
        await self.require_session(session_id)
        stmt = select(*BRIEF.c).where(
            BRIEF.c.session_id == session_id,
            BRIEF.c.tenant_id == self.tenant,
            BRIEF.c.baseline_label == label,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _version_record(row)

    # ------------------------------------------------------------------ derived projections

    async def _project(self, session_id: str, brief: Brief) -> None:
        """Rewrite the five derived tables from the saved Brief (they are never read back)."""
        tenant, version = self.tenant, brief.version
        slots: list[dict[str, Any]] = []
        for section in DICT_SECTIONS:
            for key, item in cast("dict[str, Any]", getattr(brief, section)).items():
                slots.append(
                    {
                        "session_id": session_id,
                        "slot_id": f"{section}.{key}",
                        "tenant_id": tenant,
                        "section": section,
                        "key": key,
                        "value": clean_json(item.model_dump(mode="json")["value"]),
                        "tier": item.tier,
                        "status": item.status,
                        "confidence": item.confidence,
                        "provenance": [dump_model(p) for p in item.provenance],
                        "history": [dump_model(h) for h in item.history],
                        "brief_version": version,
                    }
                )
        features = [
            {
                "session_id": session_id,
                "feature_id": ftr.id,
                "tenant_id": tenant,
                "name": ftr.name,
                "scope_status": ftr.scope_status,
                "priority": ftr.priority,
                "family": ftr.family,
                "capability_tier": ftr.capability_tier,
                "custom": ftr.custom,
                "tier": ftr.tier,
                "payload": dump_model(ftr),
                "brief_version": version,
            }
            for ftr in brief.scope
        ]
        assumptions = [
            {
                "session_id": session_id,
                "assumption_id": a.id,
                "tenant_id": tenant,
                "claim": a.claim,
                "impact": a.impact,
                "theme": a.theme,
                "decision": a.decision,
                "slot_id": a.slot_id,
                "payload": dump_model(a),
                "brief_version": version,
            }
            for a in brief.assumptions
        ]
        requests = [
            {
                "session_id": session_id,
                "request_id": r.id,
                "tenant_id": tenant,
                "topic": r.topic,
                "depth": r.depth,
                "priority": r.priority,
                "user_added": r.user_added,
                "payload": dump_model(r),
                "brief_version": version,
            }
            for r in brief.research_requests
        ]
        glossary = [
            {
                "session_id": session_id,
                "position": i,
                "tenant_id": tenant,
                "term": g.term,
                "payload": dump_model(g),
                "brief_version": version,
            }
            for i, g in enumerate(brief.glossary)
        ]
        for table, rows in (
            (SLOT, slots),
            (FEATURE, features),
            (ASSUMPTION, assumptions),
            (RESEARCH, requests),
            (GLOSSARY, glossary),
        ):
            await self._s.execute(delete(table).where(table.c.session_id == session_id))
            if rows:
                await self._s.execute(pg_insert(table).values(rows))


# --------------------------------------------------------------------------- questions


def _question_record(row: Any) -> QuestionRecord:
    return QuestionRecord(
        card_id=row.card_id,
        card=Card.model_validate(row.card),
        why=row.why,
        outcome=row.outcome,
        answer=row.answer,
        asked_at=utc(row.asked_at),
        answered_at=utc(row.answered_at),
    )


class PgQuestionRepo(_Repo):
    async def record_asked(self, session_id: str, card: Card, why: dict[str, JsonValue]) -> None:
        await self.lock_session(session_id)
        values: dict[str, Any] = {
            "card": dump_model(card),
            "why": clean_json(why),
            "outcome": "open",
            "answer": null(),
            "asked_at": self.now(),
            "answered_at": null(),
        }
        ins = pg_insert(QUESTION).values(
            session_id=session_id, card_id=card.id, tenant_id=self.tenant, **values
        )
        await self._s.execute(
            ins.on_conflict_do_update(
                index_elements=[QUESTION.c.session_id, QUESTION.c.card_id], set_=values
            )
        )

    async def record_answer(
        self, session_id: str, card_id: str, outcome: Outcome, answer: JsonValue = None
    ) -> QuestionRecord:
        await self.lock_session(session_id)
        stmt = (
            update(QUESTION)
            .where(QUESTION.c.session_id == session_id, QUESTION.c.card_id == card_id)
            .values(
                outcome=outcome,
                answer=null() if answer is None else clean_json(answer),
                answered_at=self.now(),
            )
            .returning(*QUESTION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"question {card_id} of session {session_id} not found")
        return _question_record(row)

    async def get(self, session_id: str, card_id: str) -> QuestionRecord:
        await self.require_session(session_id)
        stmt = select(*QUESTION.c).where(
            QUESTION.c.session_id == session_id,
            QUESTION.c.tenant_id == self.tenant,
            QUESTION.c.card_id == card_id,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"question {card_id} of session {session_id} not found")
        return _question_record(row)

    async def list(self, session_id: str) -> list[QuestionRecord]:
        await self.require_session(session_id)
        stmt = (
            select(*QUESTION.c)
            .where(QUESTION.c.session_id == session_id, QUESTION.c.tenant_id == self.tenant)
            .order_by(QUESTION.c.n)
        )
        return [_question_record(r) for r in (await self._s.execute(stmt)).all()]


# --------------------------------------------------------------------------- change sets, call log


class PgChangeSetRepo(_Repo):
    async def save(self, session_id: str, change_set: ChangeSet) -> None:
        await self.lock_session(session_id)
        stmt = (
            pg_insert(CHANGE_SET)
            .values(
                session_id=session_id,
                change_set_id=change_set.id,
                tenant_id=self.tenant,
                base_version=change_set.base_version,
                new_version=change_set.new_version,
                payload=dump_model(change_set),
            )
            .on_conflict_do_nothing(
                index_elements=[CHANGE_SET.c.session_id, CHANGE_SET.c.change_set_id]
            )
            .returning(CHANGE_SET.c.change_set_id)
        )
        if (await self._s.execute(stmt)).first() is None:
            raise Conflict(f"change set {change_set.id} already exists (change sets are immutable)")

    async def get(self, session_id: str, change_set_id: str) -> ChangeSet:
        await self.require_session(session_id)
        stmt = select(CHANGE_SET.c.payload).where(
            CHANGE_SET.c.session_id == session_id,
            CHANGE_SET.c.tenant_id == self.tenant,
            CHANGE_SET.c.change_set_id == change_set_id,
        )
        payload = (await self._s.execute(stmt)).scalar_one_or_none()
        if payload is None:
            raise NotFound(f"change set {change_set_id} of session {session_id} not found")
        return ChangeSet.model_validate(payload)

    async def list(self, session_id: str) -> list[ChangeSet]:
        await self.require_session(session_id)
        stmt = (
            select(CHANGE_SET.c.payload)
            .where(CHANGE_SET.c.session_id == session_id, CHANGE_SET.c.tenant_id == self.tenant)
            .order_by(CHANGE_SET.c.new_version, CHANGE_SET.c.n)
        )
        return [ChangeSet.model_validate(p) for p in (await self._s.execute(stmt)).scalars()]


class PgCallLogRepo(_Repo):
    async def record(self, session_id: str, entry: CallLogEntry) -> None:
        await self.require_session(session_id)
        await self._s.execute(
            pg_insert(CALL_LOG).values(
                session_id=session_id,
                tenant_id=self.tenant,
                call_id=entry.call_id,
                spec_id=entry.spec_id,
                spec_version=entry.spec_version,
                tokens_in=entry.tokens_in,
                tokens_out=entry.tokens_out,
                cost_micros=entry.cost_micros,
                latency_ms=entry.latency_ms,
                outcome=entry.outcome,
                reason=cast("str", clean_json(entry.reason)),
                at=utc(entry.at) or self.now(),
            )
        )

    async def list(self, session_id: str, *, limit: int = 50) -> list[CallLogEntry]:
        await self.require_session(session_id)
        stmt = (
            select(*CALL_LOG.c)
            .where(CALL_LOG.c.session_id == session_id, CALL_LOG.c.tenant_id == self.tenant)
            .order_by(CALL_LOG.c.n.desc())
            .limit(_page_limit(limit))
        )
        return [
            CallLogEntry(
                call_id=r.call_id,
                spec_id=r.spec_id,
                spec_version=r.spec_version,
                tokens_in=r.tokens_in,
                tokens_out=r.tokens_out,
                cost_micros=r.cost_micros,
                latency_ms=r.latency_ms,
                outcome=r.outcome,
                reason=r.reason,
                at=utc(r.at),
            )
            for r in (await self._s.execute(stmt)).all()
        ]


# --------------------------------------------------------------------------- sources, contributors


class PgSourceRepo(_Repo):
    async def save_source(
        self, session_id: str, source: SourceInfo, *, chunks: list[Chunk]
    ) -> bool:
        await self.lock_session(session_id)
        dup = select(SOURCE.c.id).where(
            SOURCE.c.session_id == session_id, SOURCE.c.sha256 == source.sha256
        )
        if (await self._s.execute(dup)).first() is not None:
            return False
        same_id = select(SOURCE.c.id).where(
            SOURCE.c.session_id == session_id, SOURCE.c.id == source.id
        )
        if (await self._s.execute(same_id)).first() is not None:
            raise Conflict(f"source {source.id} already exists with different content")
        for chunk in chunks:
            if chunk.source_id != source.id:
                raise ValidationFailed(
                    f"chunk {chunk.id} belongs to {chunk.source_id}, not {source.id}"
                )
        stmt = (
            pg_insert(SOURCE)
            .values(
                session_id=session_id,
                id=source.id,
                tenant_id=self.tenant,
                kind=source.kind,
                name=cast("str", clean_json(source.name)),
                sha256=source.sha256,
                payload=dump_model(source),
            )
            .on_conflict_do_nothing()
            .returning(SOURCE.c.id)
        )
        if (await self._s.execute(stmt)).first() is None:  # unreachable under the row lock
            return False
        if chunks:
            await self._s.execute(
                pg_insert(CHUNK).values(
                    [
                        {
                            "session_id": session_id,
                            "source_id": source.id,
                            "chunk_id": c.id,
                            "tenant_id": self.tenant,
                            "idx": c.index,
                            "payload": dump_model(c),
                        }
                        for c in chunks
                    ]
                )
            )
        return True

    async def get_source(self, session_id: str, source_id: str) -> SourceInfo:
        await self.require_session(session_id)
        stmt = select(SOURCE.c.payload).where(
            SOURCE.c.session_id == session_id,
            SOURCE.c.tenant_id == self.tenant,
            SOURCE.c.id == source_id,
        )
        payload = (await self._s.execute(stmt)).scalar_one_or_none()
        if payload is None:
            raise NotFound(f"source {source_id} of session {session_id} not found")
        return SourceInfo.model_validate(payload)

    async def list_sources(self, session_id: str) -> list[SourceInfo]:
        await self.require_session(session_id)
        stmt = (
            select(SOURCE.c.payload)
            .where(SOURCE.c.session_id == session_id, SOURCE.c.tenant_id == self.tenant)
            .order_by(SOURCE.c.n)
        )
        return [SourceInfo.model_validate(p) for p in (await self._s.execute(stmt)).scalars()]

    async def list_chunks(self, session_id: str, source_id: str) -> list[Chunk]:
        await self.get_source(session_id, source_id)  # NotFound for unknown sources
        stmt = (
            select(CHUNK.c.payload)
            .where(
                CHUNK.c.session_id == session_id,
                CHUNK.c.tenant_id == self.tenant,
                CHUNK.c.source_id == source_id,
            )
            .order_by(CHUNK.c.idx, CHUNK.c.chunk_id)
        )
        return [Chunk.model_validate(p) for p in (await self._s.execute(stmt)).scalars()]

    async def save_claims(self, session_id: str, claims: list[Claim]) -> None:
        await self.lock_session(session_id)
        if not claims:
            return
        rows = [
            {
                "session_id": session_id,
                "claim_id": c.id,
                "tenant_id": self.tenant,
                "source_id": c.source_id,
                "status": c.status,
                "slot_hint": c.slot_hint,
                "payload": dump_model(c),
            }
            for c in _last_by_id(claims)
        ]
        ins = pg_insert(CLAIM).values(rows)
        await self._s.execute(
            ins.on_conflict_do_update(
                index_elements=[CLAIM.c.session_id, CLAIM.c.claim_id],
                set_={
                    name: ins.excluded[name]
                    for name in ("source_id", "status", "slot_hint", "payload")
                },
            )
        )

    async def list_claims(self, session_id: str) -> list[Claim]:
        await self.require_session(session_id)
        stmt = (
            select(CLAIM.c.payload)
            .where(CLAIM.c.session_id == session_id, CLAIM.c.tenant_id == self.tenant)
            .order_by(CLAIM.c.n)
        )
        return [Claim.model_validate(p) for p in (await self._s.execute(stmt)).scalars()]


def _last_by_id(claims: Sequence[Claim]) -> list[Claim]:
    """One claim per id (the last wins): ``ON CONFLICT DO UPDATE`` cannot touch a row twice."""
    latest: dict[str, Claim] = {}
    for claim in claims:
        latest[claim.id] = claim
    return list(latest.values())


class PgContributorRepo(_Repo):
    async def upsert(self, session_id: str, contributor: Contributor) -> None:
        await self.lock_session(session_id)
        values = {"role": contributor.role, "payload": dump_model(contributor)}
        ins = pg_insert(CONTRIBUTOR).values(
            session_id=session_id,
            contributor_id=contributor.id,
            tenant_id=self.tenant,
            **values,
        )
        await self._s.execute(
            ins.on_conflict_do_update(
                index_elements=[CONTRIBUTOR.c.session_id, CONTRIBUTOR.c.contributor_id],
                set_=values,
            )
        )

    async def list(self, session_id: str) -> list[Contributor]:
        await self.require_session(session_id)
        stmt = (
            select(CONTRIBUTOR.c.payload)
            .where(CONTRIBUTOR.c.session_id == session_id, CONTRIBUTOR.c.tenant_id == self.tenant)
            .order_by(CONTRIBUTOR.c.n)
        )
        return [Contributor.model_validate(p) for p in (await self._s.execute(stmt)).scalars()]


# --------------------------------------------------------------------------- unit of work, store


class PgUnitOfWork:
    """Repositories sharing one transaction, all bound to ``tenant_id``."""

    def __init__(self, session: AsyncSession, tenant_id: str, clock: Clock) -> None:
        self._session = session
        self.tenant_id = tenant_id
        self.sessions = PgSessionRepo(session, tenant_id, clock)
        self.events = PgEventRepo(session, tenant_id, clock)
        self.briefs = PgBriefRepo(session, tenant_id, clock)
        self.questions = PgQuestionRepo(session, tenant_id, clock)
        self.change_sets = PgChangeSetRepo(session, tenant_id, clock)
        self.call_log = PgCallLogRepo(session, tenant_id, clock)
        self.sources = PgSourceRepo(session, tenant_id, clock)
        self.contributors = PgContributorRepo(session, tenant_id, clock)

    async def commit(self) -> None:
        """Commit now; the unit of work stays usable (a new transaction starts on next use)."""
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class PostgresStore:
    """``Store`` over an ``AsyncEngine`` (see ``create_store_engine``).

    ``clock`` is the time source for every timestamp and for stale-turn takeover (inject a
    ``FakeClock`` in tests).
    """

    def __init__(self, engine: AsyncEngine, *, clock: Clock | None = None) -> None:
        self._engine = engine
        self._clock: Clock = clock or SystemClock()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def clock(self) -> Clock:
        return self._clock

    @asynccontextmanager
    async def begin(self, tenant_id: str) -> AsyncIterator[PgUnitOfWork]:
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            uow = PgUnitOfWork(session, tenant_id, self._clock)
            try:
                yield uow
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
