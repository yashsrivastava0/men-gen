"""``InMemoryStore``: a faithful in-memory implementation of ``Store`` / ``UnitOfWork`` and every
repository port in ``mde_compass.store.ports`` (ADR 0007: in-memory fakes are not SQLite).

The fake is validated, not trusted: ``mde_compass.store.contract.CHECKS`` (the suite ``PostgresStore``
also passes) runs against it, so an engine or flow test built on this class says something about the
real store. Adapted from ``mde_flow.testing.store`` (credit: the Mobigen flow package). Rules kept:

* **Units of work** are transactional: mutations are applied with an undo log and rolled back on an
  exception (or ``rollback()``); ``commit()`` makes them permanent and, like Postgres, ends the
  transaction (row locks are released; later work is a new transaction).
* **Row locks.** Every write to a session's data (``save``, ``set_state``, ``add_spend``,
  ``try_begin_turn``, ``end_turn``, events, briefs, ...) takes that session's lock until the unit of
  work ends (Postgres: the row lock of ``UPDATE compass_sessions``). A second writer waits, so the
  outbound ``seq`` order equals the commit order and ``try_begin_turn`` is exclusive.
* **Tenant isolation.** A foreign or unknown session id is ``NotFound`` in every repository; a record
  bound to another tenant is a ``TenantViolation``.
* **Optimistic row_version** on ``SessionRepo.save``; consecutive-only Brief versions; idempotent
  inbound events; insert-only brief versions (only ``mark_baseline`` labels one, once).
* **Interview Memory** (``qa``, ``facts``, ``summaries``, ``batches``; ADR 0015) has no delete method.
  Writes take the session's row lock, ``next_id`` reads the stored state under that lock (so concurrent
  units get distinct ids), timestamps are normalised to UTC on write, and a fact's id, ``created_at``,
  ``statement`` and ``quote`` cannot change, exactly as the database trigger enforces.
* Records are stored as JSON and re-validated on read, so aliasing and non-serialisable fields
  cannot hide in unit tests.

Known difference: writes of an open unit of work are visible to other units immediately (Postgres is
READ COMMITTED); the engines only write under the session lock, so they cannot observe this.
``open_units`` / ``peak_open_units`` count open units of work (pool connections, in Postgres terms),
which lets a test prove nothing is held across a model call.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal, TypeVar, cast

from pydantic import JsonValue

from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.changes import ChangeSet
from mde_compass.domain.claims import Chunk, Claim, SourceInfo
from mde_compass.domain.events import StoredEvent
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.domain.interview import (
    BatchRecord,
    Fact,
    FactStatus,
    InterviewSummary,
    QaRecord,
    QaStatus,
)
from mde_compass.domain.session import Contributor, SessionRecord, SessionState
from mde_compass.store.common import (
    BATCH_ID_PREFIX,
    DEFAULT_BASELINE_LABEL,
    FACT_ID_PREFIX,
    STALE_TURN,
    changed_fact_identity,
    clean_json,
    clean_text,
    fact_immutable,
    in_utc,
    next_numbered_id,
    normalize,
    payload_text,
)
from mde_compass.store.ports import (
    BatchRepo,
    BriefRepo,
    BriefVersionRecord,
    BriefVersionSummary,
    CallLogEntry,
    CallLogRepo,
    ChangeSetRepo,
    ContributorRepo,
    EventRepo,
    FactRepo,
    HistoryPage,
    QaRepo,
    QuestionRecord,
    QuestionRepo,
    SessionRepo,
    SourceRepo,
    SummaryRepo,
    UnitOfWork,
)
from mde_core.clock import Clock, FakeClock
from mde_core.errors import Conflict, NotFound, TenantViolation, ValidationFailed
from mde_persistence.jsonutil import utc

K = TypeVar("K")
V = TypeVar("V")

Outcome = Literal["answered", "skipped", "you_decide", "back"]


class _Data:
    """The shared 'database'. Values are JSON strings; keys carry the session id."""

    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.turn_started: dict[str, datetime | None] = {}
        self.event_idx: dict[str, int] = {}
        self.events: dict[str, list[str]] = {}
        self.inbound: dict[tuple[str, str], int] = {}  # (session, event id) -> idx
        self.briefs: dict[tuple[str, int], str] = {}
        self.questions: dict[tuple[str, str], str] = {}
        self.change_sets: dict[tuple[str, str], str] = {}
        self.call_log: dict[str, list[str]] = {}
        self.sources: dict[tuple[str, str], str] = {}
        self.source_sha: dict[tuple[str, str], str] = {}  # (session, sha256) -> source id
        self.chunks: dict[tuple[str, str], list[str]] = {}
        self.claims: dict[tuple[str, str], str] = {}
        self.contributors: dict[tuple[str, str], str] = {}
        self.qa: dict[tuple[str, str, int], str] = {}  # (session, card id, version)
        self.facts: dict[tuple[str, str], str] = {}
        self.summaries: dict[str, str] = {}
        self.batches: dict[tuple[str, str], str] = {}


class InMemoryStore:
    """Implements ``mde_compass.store.ports.Store``."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock: Clock = clock or FakeClock()
        self.data = _Data()
        self._locks: dict[str, asyncio.Lock] = {}
        self.begin_count = 0
        self.open_units = 0
        self.peak_open_units = 0

    @asynccontextmanager
    async def begin(self, tenant_id: str) -> AsyncGenerator[UnitOfWork]:
        uow = _Uow(self, tenant_id)
        self.begin_count += 1
        self.open_units += 1
        self.peak_open_units = max(self.peak_open_units, self.open_units)
        try:
            yield uow
        except BaseException:
            uow.abort()
            raise
        else:
            uow.finish()
        finally:
            self.open_units -= 1

    # -- test conveniences (read-only views for assertions; they bypass tenant scoping) -----------
    def all_sessions(self) -> list[SessionRecord]:
        return [SessionRecord.model_validate_json(s) for s in self.data.sessions.values()]

    def stored_events(self, session_id: str) -> list[StoredEvent]:
        return [StoredEvent.model_validate_json(s) for s in self.data.events.get(session_id, [])]

    def lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


class _Uow:
    """One transaction bound to a tenant."""

    sessions: SessionRepo
    events: EventRepo
    briefs: BriefRepo
    questions: QuestionRepo
    change_sets: ChangeSetRepo
    call_log: CallLogRepo
    sources: SourceRepo
    contributors: ContributorRepo
    qa: QaRepo
    facts: FactRepo
    summaries: SummaryRepo
    batches: BatchRepo

    def __init__(self, store: InMemoryStore, tenant_id: str) -> None:
        self.store = store
        self.tenant = tenant_id
        self._undo: list[Callable[[], None]] = []
        self._held: dict[str, asyncio.Lock] = {}
        self.sessions = _Sessions(self)
        self.events = _Events(self)
        self.briefs = _Briefs(self)
        self.questions = _Questions(self)
        self.change_sets = _ChangeSets(self)
        self.call_log = _CallLog(self)
        self.sources = _Sources(self)
        self.contributors = _Contributors(self)
        self.qa = _Qa(self)
        self.facts = _Facts(self)
        self.summaries = _Summaries(self)
        self.batches = _Batches(self)

    # -- transaction plumbing -----------------------------------------------------------------
    async def commit(self) -> None:
        """Make the work permanent and end the transaction (locks are released)."""
        self._undo.clear()
        self._release()

    async def rollback(self) -> None:
        self.abort()

    def abort(self) -> None:
        while self._undo:
            self._undo.pop()()
        self._release()

    def finish(self) -> None:
        self._undo.clear()
        self._release()

    def _release(self) -> None:
        for lock in self._held.values():
            lock.release()
        self._held.clear()

    async def lock(self, session_id: str) -> None:
        """Hold the session's row lock until the unit of work ends."""
        if session_id in self._held:
            return
        lock = self.store.lock_for(session_id)
        await lock.acquire()
        self._held[session_id] = lock

    # -- helpers --------------------------------------------------------------------------------
    @property
    def data(self) -> _Data:
        return self.store.data

    def now(self) -> datetime:
        return self.store.clock.now()

    def own(self, tenant_id: str) -> str:
        if tenant_id != self.tenant:
            raise TenantViolation(
                "record belongs to another tenant than the unit of work is bound to",
                details={"record_tenant": tenant_id, "bound_tenant": self.tenant},
            )
        return self.tenant

    def session(self, session_id: str) -> SessionRecord | None:
        """The session if it exists in this tenant (a foreign id looks like a missing one)."""
        raw = self.data.sessions.get(session_id)
        if raw is None:
            return None
        rec = SessionRecord.model_validate_json(raw)
        return rec if rec.tenant_id == self.tenant else None

    def require_session(self, session_id: str) -> SessionRecord:
        rec = self.session(session_id)
        if rec is None:
            raise NotFound(f"session {session_id!r} not found")
        return rec

    def put(self, mapping: dict[K, V], key: K, value: V) -> None:
        """Set ``mapping[key]`` with an undo entry (an existing key keeps its position)."""
        if key in mapping:
            old = mapping[key]

            def restore() -> None:
                mapping[key] = old

            self._undo.append(restore)
        else:

            def remove() -> None:
                mapping.pop(key, None)

            self._undo.append(remove)
        mapping[key] = value

    def append(self, seq: list[V], value: V) -> None:
        seq.append(value)

        def remove() -> None:
            for i in range(len(seq) - 1, -1, -1):
                if seq[i] is value:
                    del seq[i]
                    return

        self._undo.append(remove)

    def write_session(self, rec: SessionRecord) -> SessionRecord:
        self.put(self.data.sessions, rec.id, rec.model_dump_json())
        return rec

    async def locked_session(self, session_id: str) -> SessionRecord:
        """Lock the session's row, then read it (like ``SELECT ... FOR UPDATE``)."""
        if self.session(session_id) is None:
            raise NotFound(f"session {session_id!r} not found")
        await self.lock(session_id)
        return self.require_session(session_id)


def _page_limit(limit: int) -> int:
    return max(1, limit)


# --------------------------------------------------------------------------- repositories


class _Sessions:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def create(self, record: SessionRecord) -> SessionRecord:
        self._u.own(record.tenant_id)
        record = normalize(record)
        await self._u.lock(record.id)
        if record.id in self._u.data.sessions:
            raise Conflict(f"session {record.id!r} already exists")
        now = self._u.now()
        stored = record.model_copy(
            update={
                "created_at": record.created_at or now,
                "updated_at": now,
                "outbound_seq": 0,
                "active_turn": False,
            }
        )
        self._u.write_session(stored)
        self._u.put(self._u.data.event_idx, stored.id, 0)
        self._u.put(self._u.data.turn_started, stored.id, None)
        return SessionRecord.model_validate_json(stored.model_dump_json())

    async def get(self, session_id: str) -> SessionRecord:
        return self._u.require_session(session_id)

    async def list_for_user(
        self, user_id: str, *, include_archived: bool = False, limit: int = 50
    ) -> list[SessionRecord]:
        found = [SessionRecord.model_validate_json(raw) for raw in self._u.data.sessions.values()]
        mine = [
            r
            for r in found
            if r.tenant_id == self._u.tenant
            and r.user_id == user_id
            and (include_archived or r.state != "ARCHIVED")
        ]
        mine.sort(
            key=lambda r: (r.updated_at or r.created_at or datetime.min.replace(tzinfo=UTC), r.id),
            reverse=True,
        )
        return mine[: _page_limit(limit)]

    async def save(self, record: SessionRecord) -> SessionRecord:
        self._u.own(record.tenant_id)
        record = normalize(record)
        current = await self._u.locked_session(record.id)
        if record.row_version != current.row_version:
            raise Conflict(
                f"session {record.id}: stale write (expected row_version {record.row_version},"
                f" stored {current.row_version})",
                details={"session_id": record.id, "expected_row_version": record.row_version},
            )
        saved = current.model_copy(
            update={
                "mode": record.mode,
                "locale": record.locale,
                "user_label": record.user_label,
                "title": record.title,
                "state": record.state,
                "resume_state": record.resume_state,
                "brief_version": record.brief_version,
                "approved_version": record.approved_version,
                "approved_hash": record.approved_hash,
                "contributors": list(record.contributors),
                "row_version": current.row_version + 1,
                "updated_at": self._u.now(),
            }
        )
        self._u.write_session(saved)
        return SessionRecord.model_validate_json(saved.model_dump_json())

    async def try_begin_turn(self, session_id: str) -> bool:
        current = await self._u.locked_session(session_id)
        now = self._u.now()
        started = self._u.data.turn_started.get(session_id)
        stale = started is None or now - started > STALE_TURN
        if current.active_turn and not stale:
            return False
        self._u.write_session(current.model_copy(update={"active_turn": True}))
        self._u.put(self._u.data.turn_started, session_id, now)
        return True

    async def end_turn(self, session_id: str) -> None:
        current = await self._u.locked_session(session_id)
        self._u.write_session(current.model_copy(update={"active_turn": False}))
        self._u.put(self._u.data.turn_started, session_id, None)

    async def add_spend(self, session_id: str, cost_micros: int, calls: int = 1) -> int:
        if cost_micros < 0 or calls < 0:
            raise ValidationFailed("spend and call counts cannot be negative")
        current = await self._u.locked_session(session_id)
        updated = current.model_copy(
            update={
                "spent_micros": current.spent_micros + cost_micros,
                "llm_calls": current.llm_calls + calls,
            }
        )
        self._u.write_session(updated)
        return updated.spent_micros

    async def set_state(self, session_id: str, state: SessionState) -> SessionRecord:
        current = await self._u.locked_session(session_id)
        updated = current.model_copy(
            update={
                "state": state,
                "row_version": current.row_version + 1,
                "updated_at": self._u.now(),
            }
        )
        self._u.write_session(updated)
        return SessionRecord.model_validate_json(updated.model_dump_json())


class _Events:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    def _store(self, session_id: str, event: StoredEvent) -> None:
        d = self._u.data
        d.events.setdefault(session_id, [])
        self._u.append(d.events[session_id], event.model_dump_json())
        self._u.put(d.event_idx, session_id, event.idx)

    async def record_inbound(
        self, session_id: str, event_id: str, event_type: str, payload: dict[str, JsonValue]
    ) -> bool:
        await self._u.locked_session(session_id)
        d = self._u.data
        if (session_id, event_id) in d.inbound:
            return False
        idx = d.event_idx.get(session_id, 0) + 1
        event = StoredEvent(
            idx=idx,
            direction="in",
            type=event_type,
            event_id=event_id,
            payload=cast("dict[str, JsonValue]", clean_json(payload)),
            at=self._u.now(),
        )
        self._store(session_id, event)
        self._u.put(d.inbound, (session_id, event_id), idx)
        return True

    async def append_outbound(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        turn_id: str | None = None,
    ) -> int:
        current = await self._u.locked_session(session_id)
        seq = current.outbound_seq + 1
        idx = self._u.data.event_idx.get(session_id, 0) + 1
        event = StoredEvent(
            idx=idx,
            direction="out",
            type=event_type,
            turn_id=turn_id,
            seq=seq,
            payload=cast("dict[str, JsonValue]", clean_json(payload)),
            at=self._u.now(),
        )
        self._store(session_id, event)
        self._u.write_session(current.model_copy(update={"outbound_seq": seq}))
        return seq

    def _all(self, session_id: str) -> list[StoredEvent]:
        self._u.require_session(session_id)
        return [
            StoredEvent.model_validate_json(raw) for raw in self._u.data.events.get(session_id, [])
        ]

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        out = [
            e
            for e in self._all(session_id)
            if e.direction == "out" and e.seq is not None and e.seq > after_seq
        ]
        out.sort(key=lambda e: cast("int", e.seq))
        return out[: _page_limit(limit)]

    async def list_history(
        self,
        session_id: str,
        *,
        after_idx: int = 0,
        limit: int = 50,
        q: str | None = None,
        turn_ids: list[str] | None = None,
    ) -> HistoryPage:
        needle = clean_text(q).casefold() if q else None
        wanted = {clean_text(t) for t in turn_ids} if turn_ids is not None else None
        matches: list[StoredEvent] = []
        for e in self._all(session_id):
            if e.idx <= after_idx:
                continue
            if wanted is not None:
                key = e.event_id if e.direction == "in" else e.turn_id
                if key not in wanted:
                    continue
            if needle and needle not in payload_text(e.payload):
                continue
            matches.append(e)
        matches.sort(key=lambda e: e.idx)
        size = _page_limit(limit)
        items = matches[:size]
        more = len(matches) > size
        return HistoryPage(items=items, next_after_idx=items[-1].idx if more and items else None)

    async def get_inbound(self, session_id: str, event_id: str) -> StoredEvent | None:
        for e in self._all(session_id):
            if e.direction == "in" and e.event_id == event_id:
                return e
        return None


class _Briefs:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    def _versions(self, session_id: str) -> list[BriefVersionRecord]:
        found = [
            BriefVersionRecord.model_validate_json(raw)
            for (sid, _), raw in self._u.data.briefs.items()
            if sid == session_id
        ]
        found.sort(key=lambda r: r.version)
        return found

    async def save_version(
        self, session_id: str, brief: Brief, *, change_set_id: str | None = None
    ) -> BriefVersionRecord:
        await self._u.locked_session(session_id)
        brief = normalize(brief)
        versions = self._versions(session_id)
        latest = versions[-1].version if versions else 0
        if brief.version != latest + 1:
            raise Conflict(
                f"brief version {brief.version} is not the next version (latest saved: {latest})",
                details={"session_id": session_id, "latest": latest, "got": brief.version},
            )
        record = BriefVersionRecord(
            session_id=session_id,
            version=brief.version,
            brief=brief,
            hash=brief_hash(brief),
            section_hashes=section_hashes(brief),
            baseline_label=None,
            change_set_id=change_set_id,
            created_at=self._u.now(),
        )
        self._u.put(self._u.data.briefs, (session_id, brief.version), record.model_dump_json())
        return BriefVersionRecord.model_validate_json(record.model_dump_json())

    async def get(self, session_id: str, version: int | None = None) -> BriefVersionRecord:
        self._u.require_session(session_id)
        versions = self._versions(session_id)
        if version is None:
            if not versions:
                raise NotFound(f"session {session_id} has no brief yet")
            return versions[-1]
        for record in versions:
            if record.version == version:
                return record
        raise NotFound(f"brief version {version} of session {session_id} not found")

    async def list_versions(self, session_id: str) -> list[BriefVersionSummary]:
        self._u.require_session(session_id)
        return [
            BriefVersionSummary(
                version=r.version,
                hash=r.hash,
                baseline_label=r.baseline_label,
                change_set_id=r.change_set_id,
                created_at=r.created_at,
            )
            for r in self._versions(session_id)
        ]

    async def mark_baseline(
        self, session_id: str, version: int, label: str = DEFAULT_BASELINE_LABEL
    ) -> BriefVersionRecord:
        await self._u.locked_session(session_id)
        target = await self.get(session_id, version)
        if target.baseline_label == label:
            return target
        if target.baseline_label is not None:
            raise Conflict(
                f"brief version {version} is already the baseline {target.baseline_label!r}"
            )
        for other in self._versions(session_id):
            if other.baseline_label == label:
                raise Conflict(
                    f"baseline {label!r} already labels version {other.version} (immutable)"
                )
        labelled = target.model_copy(update={"baseline_label": label})
        self._u.put(self._u.data.briefs, (session_id, version), labelled.model_dump_json())
        return BriefVersionRecord.model_validate_json(labelled.model_dump_json())

    async def get_baseline(
        self, session_id: str, label: str = DEFAULT_BASELINE_LABEL
    ) -> BriefVersionRecord | None:
        self._u.require_session(session_id)
        for record in self._versions(session_id):
            if record.baseline_label == label:
                return record
        return None


class _Questions:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def record_asked(self, session_id: str, card: Card, why: dict[str, JsonValue]) -> None:
        await self._u.locked_session(session_id)
        card = normalize(card)
        record = QuestionRecord(
            card_id=card.id,
            card=card,
            why=cast("dict[str, JsonValue]", clean_json(why)),
            outcome="open",
            asked_at=self._u.now(),
        )
        self._u.put(self._u.data.questions, (session_id, card.id), record.model_dump_json())

    async def record_answer(
        self, session_id: str, card_id: str, outcome: Outcome, answer: JsonValue = None
    ) -> QuestionRecord:
        await self._u.locked_session(session_id)
        current = await self.get(session_id, card_id)
        updated = current.model_copy(
            update={
                "outcome": outcome,
                "answer": clean_json(answer),
                "answered_at": self._u.now(),
            }
        )
        self._u.put(self._u.data.questions, (session_id, card_id), updated.model_dump_json())
        return QuestionRecord.model_validate_json(updated.model_dump_json())

    async def get(self, session_id: str, card_id: str) -> QuestionRecord:
        self._u.require_session(session_id)
        raw = self._u.data.questions.get((session_id, card_id))
        if raw is None:
            raise NotFound(f"question {card_id} of session {session_id} not found")
        return QuestionRecord.model_validate_json(raw)

    async def list(self, session_id: str) -> list[QuestionRecord]:
        self._u.require_session(session_id)
        return [
            QuestionRecord.model_validate_json(raw)
            for (sid, _), raw in self._u.data.questions.items()
            if sid == session_id
        ]


class _ChangeSets:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def save(self, session_id: str, change_set: ChangeSet) -> None:
        await self._u.locked_session(session_id)
        change_set = normalize(change_set)
        key = (session_id, change_set.id)
        if key in self._u.data.change_sets:
            raise Conflict(f"change set {change_set.id} already exists (change sets are immutable)")
        self._u.put(self._u.data.change_sets, key, change_set.model_dump_json())

    async def get(self, session_id: str, change_set_id: str) -> ChangeSet:
        self._u.require_session(session_id)
        raw = self._u.data.change_sets.get((session_id, change_set_id))
        if raw is None:
            raise NotFound(f"change set {change_set_id} of session {session_id} not found")
        return ChangeSet.model_validate_json(raw)

    async def list(self, session_id: str) -> list[ChangeSet]:
        self._u.require_session(session_id)
        found = [
            ChangeSet.model_validate_json(raw)
            for (sid, _), raw in self._u.data.change_sets.items()
            if sid == session_id
        ]
        found.sort(key=lambda c: c.new_version)  # stable: insertion order within a version
        return found


class _CallLog:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def record(self, session_id: str, entry: CallLogEntry) -> None:
        # No row lock (as in Postgres, where the insert only takes a KEY SHARE lock on the session):
        # logging a model call never waits behind a unit that is writing the session.
        self._u.require_session(session_id)
        stored = normalize(entry).model_copy(update={"at": entry.at or self._u.now()})
        self._u.data.call_log.setdefault(session_id, [])
        self._u.append(self._u.data.call_log[session_id], stored.model_dump_json())

    async def list(self, session_id: str, *, limit: int = 50) -> list[CallLogEntry]:
        self._u.require_session(session_id)
        raws = self._u.data.call_log.get(session_id, [])
        newest_first = [CallLogEntry.model_validate_json(r) for r in reversed(raws)]
        return newest_first[: _page_limit(limit)]


class _Sources:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def save_source(
        self, session_id: str, source: SourceInfo, *, chunks: list[Chunk]
    ) -> bool:
        await self._u.locked_session(session_id)
        d = self._u.data
        source = normalize(source)
        chunks = [normalize(c) for c in chunks]
        if (session_id, source.sha256) in d.source_sha:
            return False
        if (session_id, source.id) in d.sources:
            raise Conflict(f"source {source.id} already exists with different content")
        for chunk in chunks:
            if chunk.source_id != source.id:
                raise ValidationFailed(
                    f"chunk {chunk.id} belongs to {chunk.source_id}, not {source.id}"
                )
        self._u.put(d.sources, (session_id, source.id), source.model_dump_json())
        self._u.put(d.source_sha, (session_id, source.sha256), source.id)
        self._u.put(
            d.chunks,
            (session_id, source.id),
            [c.model_dump_json() for c in sorted(chunks, key=lambda c: c.index)],
        )
        return True

    async def get_source(self, session_id: str, source_id: str) -> SourceInfo:
        self._u.require_session(session_id)
        raw = self._u.data.sources.get((session_id, source_id))
        if raw is None:
            raise NotFound(f"source {source_id} of session {session_id} not found")
        return SourceInfo.model_validate_json(raw)

    async def list_sources(self, session_id: str) -> list[SourceInfo]:
        self._u.require_session(session_id)
        return [
            SourceInfo.model_validate_json(raw)
            for (sid, _), raw in self._u.data.sources.items()
            if sid == session_id
        ]

    async def list_chunks(self, session_id: str, source_id: str) -> list[Chunk]:
        await self.get_source(session_id, source_id)
        raws = self._u.data.chunks.get((session_id, source_id), [])
        return [Chunk.model_validate_json(r) for r in raws]

    async def save_claims(self, session_id: str, claims: list[Claim]) -> None:
        await self._u.locked_session(session_id)
        for claim in claims:
            claim = normalize(claim)
            self._u.put(self._u.data.claims, (session_id, claim.id), claim.model_dump_json())

    async def list_claims(self, session_id: str) -> list[Claim]:
        self._u.require_session(session_id)
        return [
            Claim.model_validate_json(raw)
            for (sid, _), raw in self._u.data.claims.items()
            if sid == session_id
        ]


class _Contributors:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def upsert(self, session_id: str, contributor: Contributor) -> None:
        await self._u.locked_session(session_id)
        contributor = normalize(contributor)
        self._u.put(
            self._u.data.contributors, (session_id, contributor.id), contributor.model_dump_json()
        )

    async def list(self, session_id: str) -> list[Contributor]:
        self._u.require_session(session_id)
        return [
            Contributor.model_validate_json(raw)
            for (sid, _), raw in self._u.data.contributors.items()
            if sid == session_id
        ]


# --------------------------------------------------------------------------- interview memory


class _Qa:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    def _rows(self, session_id: str) -> list[QaRecord]:
        """Every version of every card of the session, in insertion order."""
        return [
            QaRecord.model_validate_json(raw)
            for (sid, _, _), raw in self._u.data.qa.items()
            if sid == session_id
        ]

    async def upsert(self, session_id: str, record: QaRecord) -> None:
        await self._u.locked_session(session_id)
        record = in_utc(normalize(record), "asked_at", "answered_at")
        key = (session_id, record.card_id, record.version)
        self._u.put(self._u.data.qa, key, record.model_dump_json())  # a replace keeps its position

    async def get(self, session_id: str, card_id: str, version: int | None = None) -> QaRecord:
        self._u.require_session(session_id)
        card_id = clean_text(card_id)  # ids are stored NUL-free, so a lookup is too
        if version is not None:
            raw = self._u.data.qa.get((session_id, card_id, version))
            if raw is None:
                raise NotFound(f"question {card_id} v{version} of session {session_id} not found")
            return QaRecord.model_validate_json(raw)
        versions = [r for r in self._rows(session_id) if r.card_id == card_id]
        if not versions:
            raise NotFound(f"question {card_id} of session {session_id} not found")
        return max(versions, key=lambda r: r.version)

    async def list(
        self,
        session_id: str,
        *,
        status: QaStatus | None = None,
        batch_id: str | None = None,
        latest_only: bool = True,
    ) -> list[QaRecord]:
        self._u.require_session(session_id)
        batch_id = None if batch_id is None else clean_text(batch_id)
        rows = self._rows(session_id)
        if latest_only:
            newest: dict[str, int] = {}
            for r in rows:
                newest[r.card_id] = max(newest.get(r.card_id, 0), r.version)
            rows = [r for r in rows if r.version == newest[r.card_id]]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if batch_id is not None:
            rows = [r for r in rows if r.batch_id == batch_id]
        return rows


class _Facts:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    def _stored(self, session_id: str, fact_id: str) -> Fact:
        fact_id = clean_text(fact_id)
        raw = self._u.data.facts.get((session_id, fact_id))
        if raw is None:
            raise NotFound(f"fact {fact_id} of session {session_id} not found")
        return Fact.model_validate_json(raw)

    async def add(self, session_id: str, fact: Fact) -> None:
        await self._u.locked_session(session_id)
        fact = normalize(fact)
        key = (session_id, fact.id)
        if key in self._u.data.facts:
            raise Conflict(
                f"fact {fact.id} already exists (use update to change it)",
                details={"session_id": session_id, "fact_id": fact.id},
            )
        now = self._u.now()
        stored = fact.model_copy(
            update={"created_at": utc(fact.created_at) or now, "updated_at": now}
        )
        self._u.put(self._u.data.facts, key, stored.model_dump_json())

    async def update(self, session_id: str, fact: Fact) -> None:
        await self._u.locked_session(session_id)
        fact = normalize(fact)
        current = self._stored(session_id, fact.id)
        changed = changed_fact_identity(current, fact)
        if changed:
            raise fact_immutable(session_id, fact.id, changed)
        stored = fact.model_copy(
            update={"created_at": current.created_at, "updated_at": self._u.now()}
        )
        self._u.put(self._u.data.facts, (session_id, fact.id), stored.model_dump_json())

    async def get(self, session_id: str, fact_id: str) -> Fact:
        self._u.require_session(session_id)
        return self._stored(session_id, fact_id)

    async def list(self, session_id: str, *, status: FactStatus | None = None) -> list[Fact]:
        self._u.require_session(session_id)
        found = [
            Fact.model_validate_json(raw)
            for (sid, _), raw in self._u.data.facts.items()
            if sid == session_id
        ]
        return [f for f in found if status is None or f.status == status]

    async def next_id(self, session_id: str) -> str:
        await self._u.locked_session(session_id)
        ids = (fid for (sid, fid) in self._u.data.facts if sid == session_id)
        return next_numbered_id(FACT_ID_PREFIX, ids)


class _Summaries:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    async def get(self, session_id: str) -> InterviewSummary | None:
        self._u.require_session(session_id)
        raw = self._u.data.summaries.get(session_id)
        return None if raw is None else InterviewSummary.model_validate_json(raw)

    async def put(self, session_id: str, summary: InterviewSummary) -> None:
        await self._u.locked_session(session_id)
        stored = normalize(summary).model_copy(update={"updated_at": self._u.now()})
        self._u.put(self._u.data.summaries, session_id, stored.model_dump_json())


class _Batches:
    def __init__(self, uow: _Uow) -> None:
        self._u = uow

    def _rows(self, session_id: str) -> list[BatchRecord]:
        return [
            BatchRecord.model_validate_json(raw)
            for (sid, _), raw in self._u.data.batches.items()
            if sid == session_id
        ]

    async def add(self, session_id: str, record: BatchRecord) -> None:
        await self._u.locked_session(session_id)
        record = normalize(record)
        if (session_id, record.id) in self._u.data.batches:
            raise Conflict(
                f"batch {record.id} already exists",
                details={"session_id": session_id, "batch_id": record.id},
            )
        for other in self._rows(session_id):
            if other.mode == record.mode and other.inputs_hash == record.inputs_hash:
                raise Conflict(
                    f"batch {other.id} already has mode {record.mode!r} and the same inputs hash",
                    details={"session_id": session_id, "batch_id": other.id},
                )
        stored = record.model_copy(update={"created_at": utc(record.created_at) or self._u.now()})
        self._u.put(self._u.data.batches, (session_id, record.id), stored.model_dump_json())

    async def get(self, session_id: str, batch_id: str) -> BatchRecord:
        self._u.require_session(session_id)
        batch_id = clean_text(batch_id)
        raw = self._u.data.batches.get((session_id, batch_id))
        if raw is None:
            raise NotFound(f"batch {batch_id} of session {session_id} not found")
        return BatchRecord.model_validate_json(raw)

    async def find(self, session_id: str, mode: str, inputs_hash: str) -> BatchRecord | None:
        self._u.require_session(session_id)
        mode, inputs_hash = clean_text(mode), clean_text(inputs_hash)
        for record in self._rows(session_id):
            if record.mode == mode and record.inputs_hash == inputs_hash:
                return record
        return None

    async def list(self, session_id: str) -> list[BatchRecord]:
        self._u.require_session(session_id)
        return self._rows(session_id)

    async def next_id(self, session_id: str) -> str:
        await self._u.locked_session(session_id)
        ids = (bid for (sid, bid) in self._u.data.batches if sid == session_id)
        return next_numbered_id(BATCH_ID_PREFIX, ids)
