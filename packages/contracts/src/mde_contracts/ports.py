"""Ports: Protocols the packages implement or consume (brief 5.2: engines never import each other;
``mde_flow`` wires them through these).

Groups
- Persistence ports: ``Store`` -> ``UnitOfWork`` -> repositories (implemented by ``mde_persistence``
  on PostgreSQL; ``mde_flow.testing`` ships an in-memory fake for unit/property tests).
- Hand-off ports: ``ProjectRepoPort``, ``FrontendEnginePort``, ``InternalSourcePort``.
- Engine protocols live in ``engines.py`` (``DiscoveryEngine``, ``ResearchEngine``,
  ``SpecBuilderEngine``); the gateway protocols in ``gateway.py``.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, Protocol

from mde_contracts.common import Frozen, GateKind
from mde_contracts.events import InboundEvent, OutboundEvent, SpecReady
from mde_contracts.gates import ApprovalRecord, PlanPhase
from mde_contracts.records import (
    BriefVersionRecord,
    JobRecord,
    PlanJobPayload,
    ResearchNoteRecord,
    SessionRecord,
    SpecStatus,
    SpecVersionRecord,
)

# --------------------------------------------------------------------------- repositories


class SessionRepo(Protocol):
    async def create(self, rec: SessionRecord) -> SessionRecord: ...

    async def get(self, session_id: str, *, for_update: bool = False) -> SessionRecord | None: ...

    async def save(self, rec: SessionRecord, *, expected_version: int) -> SessionRecord:
        """Compare-and-set on ``version``; raises ``mde_core.errors.Conflict`` on mismatch.
        Returns the stored record with ``version`` incremented and ``updated_at`` set."""
        ...

    async def list_for_owner(self, owner_id: str, *, limit: int = 50) -> list[SessionRecord]: ...


class EventRepo(Protocol):
    async def record_inbound(self, session_id: str, event: InboundEvent) -> bool:
        """Idempotency: returns False (and stores nothing) when ``event_id`` was seen before."""
        ...

    async def append_outbound(self, session_id: str, event: OutboundEvent) -> OutboundEvent:
        """Assigns the next per-session ``seq`` and returns the stored event."""
        ...

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[OutboundEvent]: ...

    async def replies_to(self, session_id: str, inbound_event_id: str) -> list[OutboundEvent]:
        """Outbound events produced for an inbound event (answers duplicate submissions)."""
        ...


class BriefRepo(Protocol):
    async def add(self, rec: BriefVersionRecord) -> BriefVersionRecord: ...

    async def get(self, session_id: str, version: int) -> BriefVersionRecord | None: ...

    async def latest(self, session_id: str) -> BriefVersionRecord | None: ...

    async def set_status(self, session_id: str, version: int, status: str) -> None: ...


class ResearchRepo(Protocol):
    async def add(self, rec: ResearchNoteRecord) -> ResearchNoteRecord: ...

    async def latest(self, session_id: str) -> ResearchNoteRecord | None: ...


class SpecRepo(Protocol):
    async def add(self, rec: SpecVersionRecord) -> SpecVersionRecord: ...

    async def get(self, session_id: str, version: int) -> SpecVersionRecord | None: ...

    async def latest(self, session_id: str) -> SpecVersionRecord | None: ...

    async def list_versions(self, session_id: str) -> list[SpecVersionRecord]: ...

    async def set_status(
        self,
        session_id: str,
        version: int,
        status: SpecStatus,
        *,
        git_sha: str | None = None,
        approved_at: datetime | None = None,
    ) -> SpecVersionRecord:
        """The only mutation allowed on a stored version (approved versions are immutable).
        Legal moves: draft -> approved | superseded; approved -> handed_off | superseded.
        ``handed_off`` and ``superseded`` are terminal; moving to ``approved`` stamps
        ``approved_at``; anything else raises ``Conflict``."""
        ...


class ApprovalRepo(Protocol):
    async def add(self, rec: ApprovalRecord) -> ApprovalRecord: ...

    async def list(
        self,
        session_id: str,
        gate: GateKind,
        *,
        version: int | None = None,
        hash: str | None = None,
    ) -> list[ApprovalRecord]: ...


class JobRepo(Protocol):
    async def enqueue_plan(
        self, session_id: str, tenant_id: str, payload: PlanJobPayload
    ) -> JobRecord:
        """Raises ``Conflict`` if the session already has an active (queued/running) run."""
        ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def active_for_session(self, session_id: str) -> JobRecord | None: ...

    async def claim(self, worker_id: str) -> JobRecord | None:
        """``FOR UPDATE SKIP LOCKED``; system scope only."""
        ...

    async def heartbeat(self, job_id: str, worker_id: str) -> None: ...

    async def set_phase(self, job_id: str, phase: PlanPhase) -> None: ...

    async def complete(self, job_id: str, *, error: str | None = None) -> None:
        """Close a run. ``error=None`` = succeeded. With an error: the job is requeued while
        ``attempts < max_attempts``, then failed; if cancellation was requested it ends
        ``cancelled`` (no retry). Only a RUNNING job can be completed: completing a queued or already
        closed job raises ``Conflict`` (a never-started job is retired with ``cancel``)."""
        ...

    async def cancel(self, session_id: str) -> bool:
        """Cooperative cancel of the session's active run. A job no worker has started is cancelled
        outright (state ``cancelled``, the run slot is freed); a running one is flagged
        (``cancel_requested``) and ends when the worker checks. True if a run existed."""
        ...

    async def is_cancel_requested(self, job_id: str) -> bool: ...

    async def recover_stuck(self, *, older_than_seconds: float) -> int:
        """Requeue running jobs whose heartbeat is stale; returns the number recovered."""
        ...


class UnitOfWork(Protocol):
    sessions: SessionRepo
    events: EventRepo
    briefs: BriefRepo
    research: ResearchRepo
    specs: SpecRepo
    approvals: ApprovalRepo
    jobs: JobRepo

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class Store(Protocol):
    def begin(self, tenant_id: str | None) -> AbstractAsyncContextManager[UnitOfWork]:
        """Open a unit of work bound to one tenant (every repository filters by it).
        ``None`` is system scope: only the job worker and ops may use it.
        Commits on clean exit, rolls back on exception."""
        ...


# --------------------------------------------------------------------------- budget port


class BudgetVerdict(Frozen):
    """Answer of the budget gate: may a new planning run start? (spec 9: 'Enterprise: budget cap
    reached -> pause before the Plan phase; explain; ask an approver')."""

    ok: bool
    reason: Literal["ok", "cap_reached", "tripwire"] = "ok"
    message: str | None = None  # plain-language, safe to show


class BudgetGate(Protocol):
    """Implemented by the composition root from the LLM Gateway's budget status; the flow only
    sees this port (it never imports the gateway)."""

    async def check(self, *, session_id: str, tenant_id: str) -> BudgetVerdict: ...


# --------------------------------------------------------------------------- hand-off ports


class CommitInfo(Frozen):
    sha: str
    path: str  # repo-relative path of APP_SPEC.md
    repo_dir: str


class ProjectRepoPort(Protocol):
    async def commit_spec(
        self,
        *,
        tenant_id: str,
        app_id: str,
        spec_text: str,
        version: int,
        spec_hash: str,
        message: str,
    ) -> CommitInfo:
        """Write APP_SPEC.md and spec.lock.json (version, hash) into the app's git repo and commit.
        Idempotent for the same (version, hash)."""
        ...


class FrontendEnginePort(Protocol):
    async def notify(self, event: SpecReady) -> None:
        """Called exactly when ``spec_ready`` is emitted (never before Gate 2 approval)."""
        ...


class InternalFact(Frozen):
    statement: str
    source: str  # connector + document reference


class InternalSourcePort(Protocol):
    """R6 (internal sources): interface only in v1; a stub implementation is provided."""

    async def fetch(
        self, query: str, *, tenant_id: str, connectors: list[str]
    ) -> list[InternalFact]: ...
