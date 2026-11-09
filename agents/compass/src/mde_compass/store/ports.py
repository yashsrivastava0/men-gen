"""Store ports (Protocols). Implemented twice: a PostgreSQL store and an in-memory fake, both validated by
ONE shared contract suite (repo ADR 0007). Engines and the API only ever see these.

Rules every implementation keeps (prompt 5.8):
* Every unit of work is bound to a tenant; a foreign id behaves as not found (``NotFound``).
* One active turn per session (``try_begin_turn`` is atomic: advisory lock + unique partial index in PG).
* Optimistic ``version`` on the Brief: ``save_version`` raises ``Conflict`` if the stored latest version is
  not ``brief.version - 1``. A saved version row is never updated (append-only); only ``mark_baseline``
  sets a label, once.
* Inbound events are idempotent by ``event_id``; outbound events get a per-session strictly increasing ``seq``
  equal to commit order.
* Transactions never span a model call (ADR 0004): open a unit of work, read/write, commit, then call.
* Errors: ``mde_core.errors.NotFound`` / ``Conflict`` / ``TenantViolation``.
* Tables are prefixed ``compass_``; derived projections (slots, features, assumptions, research requests,
  glossary) are rewritten by ``BriefRepo.save_version`` and never read back.
* Interview Memory (ADR 0015): ``qa``, ``facts``, ``summaries`` and ``batches``. Nothing in them is ever
  deleted (no delete method exists): a correction adds a new Q&A version or a superseding fact. Every write
  takes the session's row lock, so listing order (insertion order) equals commit order.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.changes import ChangeSet
from mde_compass.domain.claims import Chunk, Claim, SourceInfo
from mde_compass.domain.common import Strict
from mde_compass.domain.events import StoredEvent
from mde_compass.domain.interview import (
    BatchRecord,
    Fact,
    FactStatus,
    InterviewSummary,
    QaRecord,
    QaStatus,
)
from mde_compass.domain.session import Contributor, SessionRecord, SessionState


class BriefVersionRecord(Strict):
    session_id: str
    version: int
    brief: Brief
    hash: str
    section_hashes: dict[str, str]
    baseline_label: str | None = None
    change_set_id: str | None = None
    created_at: datetime | None = None


class BriefVersionSummary(Strict):
    version: int
    hash: str
    baseline_label: str | None = None
    change_set_id: str | None = None
    created_at: datetime | None = None


class QuestionRecord(Strict):
    card_id: str  # Q-###
    card: Card
    why: dict[str, JsonValue] = {}  # scores and the reason this card was asked
    outcome: Literal["open", "answered", "skipped", "you_decide", "back"] = "open"
    answer: JsonValue = None
    asked_at: datetime | None = None
    answered_at: datetime | None = None


class CallLogEntry(Strict):
    call_id: str  # request id, ``call_<ulid>``
    spec_id: str  # ``cmp.write_card``
    spec_version: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    outcome: Literal["ok", "repaired", "fallback", "error", "cache_hit"] = "ok"
    reason: str = ""
    at: datetime | None = None


class HistoryPage(Strict):
    items: list[StoredEvent]
    next_after_idx: int | None = None


class SessionRepo(Protocol):
    async def create(self, record: SessionRecord) -> SessionRecord: ...
    async def get(self, session_id: str) -> SessionRecord: ...
    async def list_for_user(
        self, user_id: str, *, include_archived: bool = False, limit: int = 50
    ) -> list[SessionRecord]: ...
    async def save(self, record: SessionRecord) -> SessionRecord:
        """Optimistic: ``Conflict`` unless ``record.row_version`` equals the stored one. Returns the saved
        record with ``row_version + 1``."""
        ...

    async def try_begin_turn(self, session_id: str) -> bool:
        """Atomically claim the single active turn. False if another turn is running."""
        ...

    async def end_turn(self, session_id: str) -> None: ...
    async def add_spend(self, session_id: str, cost_micros: int, calls: int = 1) -> int:
        """Add to the session's spend and call counter; returns the new ``spent_micros``."""
        ...

    async def set_state(self, session_id: str, state: SessionState) -> SessionRecord: ...


class EventRepo(Protocol):
    async def record_inbound(
        self, session_id: str, event_id: str, event_type: str, payload: dict[str, JsonValue]
    ) -> bool:
        """Append an inbound event. False (and nothing written) if ``event_id`` was already recorded."""
        ...

    async def append_outbound(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        turn_id: str | None = None,
    ) -> int:
        """Append an outbound event; returns its per-session SSE ``seq`` (1, 2, 3, ...)."""
        ...

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]: ...
    async def list_history(
        self,
        session_id: str,
        *,
        after_idx: int = 0,
        limit: int = 50,
        q: str | None = None,
        turn_ids: list[str] | None = None,
    ) -> HistoryPage:
        """Both directions in order. ``q`` filters by case-insensitive substring of the payload text;
        ``turn_ids`` restricts to those inbound events and the outbound events that answered them."""
        ...

    async def get_inbound(self, session_id: str, event_id: str) -> StoredEvent | None: ...


class BriefRepo(Protocol):
    async def save_version(
        self, session_id: str, brief: Brief, *, change_set_id: str | None = None
    ) -> BriefVersionRecord:
        """Append version ``brief.version``. ``Conflict`` unless it equals latest + 1 (first save: 1).
        The hash and section hashes are computed here with ``mde_compass.domain.hashing``."""
        ...

    async def get(self, session_id: str, version: int | None = None) -> BriefVersionRecord:
        """The given version, or the latest. ``NotFound`` if none."""
        ...

    async def list_versions(self, session_id: str) -> list[BriefVersionSummary]: ...
    async def mark_baseline(
        self, session_id: str, version: int, label: str = "B-G1"
    ) -> BriefVersionRecord:
        """Label a version as an approved baseline, once; labelling a different version with the same
        label raises ``Conflict`` (approved versions are immutable)."""
        ...

    async def get_baseline(
        self, session_id: str, label: str = "B-G1"
    ) -> BriefVersionRecord | None: ...


class QuestionRepo(Protocol):
    async def record_asked(
        self, session_id: str, card: Card, why: dict[str, JsonValue]
    ) -> None: ...
    async def record_answer(
        self,
        session_id: str,
        card_id: str,
        outcome: Literal["answered", "skipped", "you_decide", "back"],
        answer: JsonValue = None,
    ) -> QuestionRecord: ...
    async def get(self, session_id: str, card_id: str) -> QuestionRecord: ...
    async def list(self, session_id: str) -> list[QuestionRecord]: ...


class ChangeSetRepo(Protocol):
    async def save(self, session_id: str, change_set: ChangeSet) -> None: ...
    async def get(self, session_id: str, change_set_id: str) -> ChangeSet: ...
    async def list(self, session_id: str) -> list[ChangeSet]: ...


class CallLogRepo(Protocol):
    async def record(self, session_id: str, entry: CallLogEntry) -> None: ...
    async def list(self, session_id: str, *, limit: int = 50) -> list[CallLogEntry]: ...


class SourceRepo(Protocol):
    async def save_source(
        self, session_id: str, source: SourceInfo, *, chunks: list[Chunk]
    ) -> bool:
        """False (nothing written) if a source with the same sha256 already exists (de-duplication)."""
        ...

    async def get_source(self, session_id: str, source_id: str) -> SourceInfo: ...
    async def list_sources(self, session_id: str) -> list[SourceInfo]: ...
    async def list_chunks(self, session_id: str, source_id: str) -> list[Chunk]: ...
    async def save_claims(self, session_id: str, claims: list[Claim]) -> None: ...
    async def list_claims(self, session_id: str) -> list[Claim]: ...


class ContributorRepo(Protocol):
    async def upsert(self, session_id: str, contributor: Contributor) -> None: ...
    async def list(self, session_id: str) -> list[Contributor]: ...


class QaRepo(Protocol):
    """The question and answer ledger, versioned by ``(card_id, version)``. An edit is a new version
    (the engine marks the old one ``superseded``); rows are never deleted."""

    async def upsert(self, session_id: str, record: QaRecord) -> None:
        """Insert the record, or replace the row with the same ``(card_id, version)`` (it keeps its
        place in the insertion order)."""
        ...

    async def get(self, session_id: str, card_id: str, version: int | None = None) -> QaRecord:
        """The given version, or the newest one when ``version`` is None. ``NotFound`` if none."""
        ...

    async def list(
        self,
        session_id: str,
        *,
        status: QaStatus | None = None,
        batch_id: str | None = None,
        latest_only: bool = True,
    ) -> list[QaRecord]:
        """Insertion order. With ``latest_only`` (the default) each card contributes only its newest
        version, and the filters then apply to that row ("the open questions" are the cards whose
        current version is open). With ``latest_only=False`` every version is a candidate."""
        ...


class FactRepo(Protocol):
    """The long-term memory. ``id``, ``created_at``, ``statement`` and ``quote`` of a stored fact never
    change; there is no delete."""

    async def add(self, session_id: str, fact: Fact) -> None:
        """``Conflict`` if the id exists. ``created_at`` defaults to now; ``updated_at`` is now."""
        ...

    async def update(self, session_id: str, fact: Fact) -> None:
        """Replace the mutable fields (status, links, metadata); ``updated_at`` becomes now.
        ``NotFound`` if the fact is absent; ``Conflict`` if ``statement`` or ``quote`` differ from the
        stored fact, or ``created_at`` is set and differs (``None`` means "unchanged")."""
        ...

    async def get(self, session_id: str, fact_id: str) -> Fact: ...
    async def list(self, session_id: str, *, status: FactStatus | None = None) -> list[Fact]:
        """Insertion order, optionally only the facts in one status."""
        ...

    async def next_id(self, session_id: str) -> str:
        """``FCT-###``: the highest existing number plus one over ALL facts (superseded ones included), so
        an id is never reused. It reads the stored state (it reserves nothing) after taking the session's
        row lock, so concurrent writers get distinct ids as long as each adds its fact in the same unit
        of work."""
        ...


class SummaryRepo(Protocol):
    """The rolling interview summary: one row per session, replaced on each refresh."""

    async def get(self, session_id: str) -> InterviewSummary | None: ...
    async def put(self, session_id: str, summary: InterviewSummary) -> None:
        """Replace the summary. ``updated_at`` is set to now by the store."""
        ...


class BatchRepo(Protocol):
    """Planner runs. ``(mode, inputs_hash)`` is unique per session, so a replan with identical inputs is
    found, not repeated."""

    async def add(self, session_id: str, record: BatchRecord) -> None:
        """``Conflict`` if the id exists or the same ``(mode, inputs_hash)`` was already recorded.
        ``created_at`` defaults to now."""
        ...

    async def get(self, session_id: str, batch_id: str) -> BatchRecord: ...
    async def find(self, session_id: str, mode: str, inputs_hash: str) -> BatchRecord | None: ...
    async def list(self, session_id: str) -> list[BatchRecord]:
        """Insertion order."""
        ...

    async def next_id(self, session_id: str) -> str:
        """``BAT-###``: highest existing number plus one (same rules as ``FactRepo.next_id``)."""
        ...


class UnitOfWork(Protocol):
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

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class Store(Protocol):
    def begin(self, tenant_id: str) -> AbstractAsyncContextManager[UnitOfWork]:
        """Open a unit of work for a tenant. Commits on clean exit, rolls back on any exception."""
        ...
