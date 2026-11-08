"""``FlowService``: the flow controller facade the context API wraps (brief 8.1).

Everything that changes a session goes through here, and every state change goes through
``mde_flow.machine``. The service owns:

* the Discover turn loop wiring (``DiscoveryEngine``), Gate 1 and Gate 2 (``mde_flow.gates``),
* the one plan run per session (``mde_flow.runs``) and its two phases (research, build) through
  the injected ``ResearchEngine`` / ``SpecBuilderEngine``,
* the hand-off: ``spec_ready`` exists **only** in ``_handoff`` below, which runs only after
  ``gates.assert_handoff_allowed`` passed for the current hash and ``ProjectRepoPort.commit_spec``
  succeeded; the frontend is notified after the unit of work commits.

Transaction model (see ``docs/architecture/flow-and-gates.md``). **No engine (LLM) call ever runs
inside a unit of work**: a unit of work pins one pooled connection, and an engine call can take
tens of seconds. A turn is therefore a *replayed* transaction (``_run_turn``):

1. open a unit of work, lock the session row, record the inbound event (idempotent), and run the
   turn logic against the stored state;
2. the first time the logic needs an engine, it raises ``_NeedEngine``: the unit of work rolls
   back, the engine runs with **no connection held**, and its result is appended to the turn's
   journal;
3. the turn logic runs again from the stored state, now served the journalled results (a request
   that differs from the journalled one means the state moved, so the journal is dropped), and so
   on, until one pass needs no more engines: it saves the session with a compare-and-set, appends
   the outbound events and commits. Only that last pass writes anything durable.

A pass that loses a compare-and-set race re-reads and retries (bounded); an event is recorded
together with its effects, so a crash or an engine failure leaves nothing behind and a retried
event id is processed from scratch. A duplicate of an event that is still in flight (this
process) is answered at once from what is stored (possibly nothing yet: an in-progress notice);
a duplicate across processes loses at the idempotent insert and returns the stored replies.
Events of one session are processed one at a time per process (``RunManager.session_lock``).

The plan job works in short units of work between engine calls and re-checks, under the same row
lock, that it is still the session's current run (running, owned by this worker, not cancelled)
before it writes.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final, Literal, Protocol, TypeVar, cast, get_args, runtime_checkable

from pydantic import BaseModel, JsonValue

from mde_contracts.brief import Brief, BriefRow
from mde_contracts.calls import ChangeOp
from mde_contracts.cards import CardInstance
from mde_contracts.common import GateKind, Mode, Principal
from mde_contracts.engines import (
    BuildRequest,
    BuildResult,
    DiscoveryEngine,
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryState,
    ResearchEngine,
    ResearchRequest,
    RunContext,
    SpecBuilderEngine,
    SpecSection,
)
from mde_contracts.events import (
    AssistantMessage,
    BriefPage,
    CardEvent,
    GateDecision,
    InboundEvent,
    Notice,
    OptionSelected,
    OutboundEvent,
    PlanPage,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
    UiAction,
    UserMessage,
)
from mde_contracts.gates import ApprovalRecord, ApprovalVia, GateStatus, PlanPhase, RunOrigin
from mde_contracts.plan import PlanView
from mde_contracts.policy import Policy
from mde_contracts.ports import (
    BudgetGate,
    BudgetVerdict,
    FrontendEnginePort,
    ProjectRepoPort,
    Store,
    UnitOfWork,
)
from mde_contracts.records import (
    BriefVersionRecord,
    JobRecord,
    PlanJobPayload,
    ResearchNoteRecord,
    SessionRecord,
    SpecVersionRecord,
)
from mde_contracts.research import ResearchJobId, ResearchNote
from mde_contracts.slots import SLOT_IDS, SlotData, SlotRecord, SlotSource
from mde_core.cancel import CancellationToken
from mde_core.clock import Clock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    Conflict,
    GateError,
    HashMismatch,
    NotFound,
    ValidationFailed,
)
from mde_core.hashing import sha256_hex
from mde_core.logging import get_logger
from mde_flow import bookkeeping, gates, machine, messages, stakeholders
from mde_flow.bookkeeping import BudgetHold, HoldKind
from mde_flow.dependency_map import (
    SECTION_ORDER,
    ChangePlan,
    classify_post_handoff,
    plan_change,
)
from mde_flow.diffing import brief_diff, diff_ids, plan_diff, spec_ids
from mde_flow.errors import Forbidden, HandoffFailed
from mde_flow.inputs import brand_url, references
from mde_flow.machine import FlowTransition
from mde_flow.models import (
    GateSnapshot,
    JobIntent,
    JobSummary,
    SessionSnapshot,
    SlotSummary,
    VersionInfo,
    decode_intent,
    encode_intent,
)
from mde_flow.runs import RunManager
from mde_flow.screening import screen_event
from mde_spec_loader.hashing import compute_spec_hash

log = get_logger("mde_flow")

DEFAULT_POLICY_IDS: Final[dict[Mode, str]] = {
    "guided": "guided-default",
    "enterprise": "enterprise-default",
}
RESEARCH_STALE_DAYS: Final = 14  # spec 3.5: an older Research Note is refreshed at the next run
_CONTROL_ACTIONS: Final = frozenset({"stop", "pause", "resume", "restart", "archive"})
_SECTION_BY_NAME: Final[dict[str, SpecSection]] = {s: s for s in SECTION_ORDER}
_JOB_BY_NAME: Final[dict[str, ResearchJobId]] = {j: j for j in get_args(ResearchJobId)}

PostAction = Callable[[], Awaitable[None]]
_PLACEHOLDER = "-"
#: replies that are not the answer to a builder's question before "use the default" is offered
MAX_UNANSWERED_REPLIES: Final = 3
#: Discover's reading of a typed reply that counts as an answer to a card's question
_ANSWER_INTENTS: Final = frozenset({"answer", "volunteered", "change", "idea", "approve"})
_BUDGET_OK: Final = BudgetVerdict(ok=True)


R = TypeVar("R", bound=BaseModel)
MAX_TURN_RETRIES: Final = 5  # times one turn starts over because the state moved under it
MAX_ENGINE_CALLS_PER_TURN: Final = 200  # safety net: one pass per call, so this bounds the loop


class _ResearchReport(Protocol):
    """What ``DefaultResearchEngine.run_with_report`` returns, as far as the flow reads it."""

    @property
    def note(self) -> ResearchNote: ...

    @property
    def injection_events(self) -> Sequence[object]: ...


@runtime_checkable
class _ReportingResearch(Protocol):
    """A research engine that also says what it ignored (spec 9: "noted in the event log").

    ``ResearchEngine.run`` returns the note only. An engine that has ``run_with_report`` (the
    default one does) lets the flow tell the user, in plain words, that instructions found in a
    page were not followed. The flow never imports the engine: it checks for the method."""

    async def run_with_report(self, req: ResearchRequest, ctx: RunContext) -> _ResearchReport: ...


@dataclass(frozen=True)
class _Recorded:
    """One engine result journalled by an earlier pass of the same turn."""

    name: str
    fingerprint: str
    result: BaseModel


class _NeedEngine(Exception):
    """Control flow: the pass needs an engine result it does not have. Not an error."""

    def __init__(self, name: str, fingerprint: str, call: Callable[[], Awaitable[BaseModel]]):
        super().__init__(name)
        self.name = name
        self.fingerprint = fingerprint
        self._call = call

    async def run(self) -> _Recorded:
        return _Recorded(self.name, self.fingerprint, await self._call())


class _Retry(Exception):
    """Control flow: the pass saw state it cannot build on (stale journal, lost compare-and-set)."""


@dataclass
class _Outcome:
    stored: list[OutboundEvent]
    turn: _Turn | None  # None: nothing to run after the commit (a duplicate's stored replies)


@dataclass
class _Turn:
    """Working state of one pass of a turn (one inbound event, or one step of a plan job)."""

    uow: UnitOfWork
    rec: SessionRecord
    policy: Policy
    reply_to: str | None
    ctx: RunContext
    journal: list[_Recorded]
    principal: Principal | None = None
    cursor: int = 0
    original: SessionRecord = field(init=False)
    out: list[OutboundEvent] = field(default_factory=list[OutboundEvent])
    post: list[PostAction] = field(default_factory=list[PostAction])
    settled: int = 0  # stakeholder disagreements announced in this pass (see ``_settle``)

    def __post_init__(self) -> None:
        self.original = self.rec


def _fingerprint(name: str, *parts: object) -> str:
    """Identity of an engine request. Event ids are left out: the flow mints a fresh id for the
    messages it feeds an engine, and that must not make a replayed request look different."""

    def plain(part: object) -> object:
        if isinstance(part, BaseModel):
            return part.model_dump(mode="json", exclude={"event_id"})
        if isinstance(part, list | tuple):
            return [plain(p) for p in cast("Sequence[object]", part)]
        return part

    return sha256_hex(json.dumps([name, [plain(p) for p in parts]], sort_keys=True, default=str))


@dataclass(frozen=True)
class _JobCtx:
    job: JobRecord
    worker_id: str
    token: CancellationToken
    #: spec version numbers this run reserved with a committed draft before writing the project
    #: repo (see ``_job_done``); a run that ends without using them marks those drafts superseded
    burned: list[int] = field(default_factory=list[int])

    @property
    def tenant_id(self) -> str:
        return self.job.tenant_id

    @property
    def session_id(self) -> str:
        return self.job.session_id


class FlowService:
    """The flow controller. See the module docstring for the guarantees."""

    def __init__(
        self,
        *,
        store: Store,
        discovery: DiscoveryEngine,
        research: ResearchEngine,
        spec_builder: SpecBuilderEngine,
        project_repo: ProjectRepoPort,
        frontend: FrontendEnginePort,
        policies: Callable[[str], Policy],
        clock: Clock,
        ids: Callable[[str], str],
        reminders: gates.ReminderPort | None = None,
        runs: RunManager | None = None,
        budget_gate: BudgetGate | None = None,
    ) -> None:
        self.store = store
        self.discovery = discovery
        self.research = research
        self.builder = spec_builder
        self.project_repo = project_repo
        self.frontend = frontend
        self.policies = policies
        self.clock = clock
        self.ids = ids
        self.reminders: gates.ReminderPort = reminders or gates.NoopReminders()
        self.runs = runs or RunManager()
        self.budget_gate = budget_gate  # None: nothing pauses planning (the gateway still refuses)
        self._inflight: set[tuple[str, str]] = set()  # (session id, event id) being processed

    # ================================================================== public API

    async def create_session(
        self,
        principal: Principal,
        mode: Mode,
        policy_id: str | None = None,
        org_profile: dict[str, JsonValue] | None = None,
    ) -> tuple[SessionRecord, list[OutboundEvent]]:
        """Create a session in DISCOVERING; its first reply is the engine's canned greeting.

        ``org_profile`` is the tenant's Org Profile (spec 10): it goes to Discover on this first
        call only, and only for an Enterprise session (a Guided session never carries it). Discover
        turns it into ``assumed`` slots, so later turns read it from the slots, not from here.

        The greeting comes from the engine before the session is stored, so the one unit of work
        that creates the session, adopts the engine's state and appends the greeting never waits
        on an engine."""
        pid = policy_id or DEFAULT_POLICY_IDS[mode]
        policy = self.policies(pid)
        if policy.mode != mode:
            raise ValidationFailed(
                f"policy {pid!r} is a {policy.mode} policy, not {mode}", details={"policy_id": pid}
            )
        now = self.clock.now()
        rec = SessionRecord(
            id=self.ids("ses"),
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            app_id=self.ids("app"),
            mode=mode,
            policy_id=pid,
            created_at=now,
            updated_at=now,
        )
        ctx = self._ctx(rec)
        profile = org_profile if mode == "enterprise" else None
        request = self._dreq_for(rec, policy, org_profile=profile)
        result = await self.discovery.start(request, ctx)  # no connection held
        rec = self._adopt_state(rec, result)
        async with self.store.begin(principal.tenant_id) as uow:
            rec = await uow.sessions.create(rec)
            turn = self._turn(
                uow, rec, policy, reply_to=None, ctx=ctx, journal=[], principal=principal
            )
            self._adopt(turn, result.events)
            stored = await self._flush(turn)
        await self._after_commit(turn, stored)
        return turn.rec, stored

    async def handle_event(
        self, principal: Principal, session_id: str, event: InboundEvent
    ) -> list[OutboundEvent]:
        """Process one inbound event and return the outbound events it produced.

        A repeated ``event_id`` is not processed again: the original replies are returned (or,
        while the first delivery is still in flight, whatever is stored by now: possibly an
        in-progress notice). No unit of work is open while an engine runs (module docstring).
        """
        event, redacted = screen_event(event)  # no secret is recorded, shown or summarised
        key = (session_id, event.event_id)
        if key in self._inflight:
            return await self._answer_in_flight_duplicate(principal, session_id, event)
        self._inflight.add(key)
        try:
            async with self.runs.session_lock(session_id):
                ctx = RunContext(
                    session_id=session_id,
                    tenant_id=principal.tenant_id,
                    request_id=self.ids("req"),
                )

                async def attempt(uow: UnitOfWork, journal: list[_Recorded]) -> _Outcome:
                    return await self._event_pass(
                        uow, principal, session_id, event, ctx, journal, redacted
                    )

                return (await self._run_turn(principal.tenant_id, attempt)).stored
        finally:
            self._inflight.discard(key)

    async def _answer_in_flight_duplicate(
        self, principal: Principal, session_id: str, event: InboundEvent
    ) -> list[OutboundEvent]:
        """The same event id is being processed right now: never a second effect."""
        async with self.store.begin(principal.tenant_id) as uow:
            rec = await uow.sessions.get(session_id)
            if rec is None:
                raise NotFound(f"session {session_id!r} not found")
            self._authorize(principal, rec, self.policies(rec.policy_id), event)
            replies = await uow.events.replies_to(session_id, event.event_id)
        if replies:
            return replies
        return [
            Notice(
                event_id=self.ids("evt"),
                session_id=session_id,
                at=self.clock.now(),
                in_reply_to=event.event_id,
                code=messages.N_IN_PROGRESS,
                text=messages.IN_PROGRESS,
            )
        ]

    async def _event_pass(
        self,
        uow: UnitOfWork,
        principal: Principal,
        session_id: str,
        event: InboundEvent,
        ctx: RunContext,
        journal: list[_Recorded],
        redacted: bool = False,
    ) -> _Outcome:
        """One pass of an event's turn: lock, record the event with its effects, run the logic."""
        rec = await uow.sessions.get(session_id, for_update=True)
        if rec is None:
            raise NotFound(f"session {session_id!r} not found")
        policy = self.policies(rec.policy_id)
        self._authorize(principal, rec, policy, event)
        if not await uow.events.record_inbound(session_id, event):
            return _Outcome(await uow.events.replies_to(session_id, event.event_id), None)
        turn = self._turn(
            uow, rec, policy, reply_to=event.event_id, ctx=ctx, journal=journal, principal=principal
        )
        if redacted:
            self._notice(turn, messages.N_INPUT_CLEANED, messages.INPUT_CLEANED, "warn")
        await self._dispatch(turn, event)
        return _Outcome(await self._flush(turn), turn)

    async def _run_turn(
        self,
        tenant_id: str,
        attempt: Callable[[UnitOfWork, list[_Recorded]], Awaitable[_Outcome]],
    ) -> _Outcome:
        """Run ``attempt`` as a transaction that is replayed around its engine calls.

        Each pass is a whole unit of work. A pass that needs an engine result it does not have is
        rolled back, the engine runs with no connection held, and the pass is repeated with the
        result journalled. A pass that finds the state moved (a request that differs from the
        journalled one, a lost compare-and-set) starts over with an empty journal, at most
        ``MAX_TURN_RETRIES`` times; after that the caller gets a ``Conflict`` and may retry the
        event. There is one pass per engine call, so a turn costs (calls + 1) short units of work.
        """
        journal: list[_Recorded] = []
        retries = 0
        while True:
            try:
                async with self.store.begin(tenant_id) as uow:
                    outcome = await attempt(uow, journal)
            except _NeedEngine as need:
                if len(journal) >= MAX_ENGINE_CALLS_PER_TURN:
                    raise Conflict("this turn needs too many engine calls") from None
                journal.append(await need.run())  # no unit of work is open here
                continue
            except _Retry:
                retries += 1
                if retries > MAX_TURN_RETRIES:
                    raise Conflict("this session is changing too quickly, try again") from None
                journal.clear()
                continue
            if outcome.turn is not None:
                await self._after_commit(outcome.turn, outcome.stored)
            return outcome

    async def run_plan_job(self, job: JobRecord, worker_id: str, cancel: CancellationToken) -> None:
        """Run one claimed plan job: research, then build, then open Gate 2 (or hand off a light
        post-handoff change). Cooperative: ``cancel`` and the database flag are checked between
        steps and before every engine call. ``job`` is the record ``JobRepo.claim`` returned
        (running, owned by ``worker_id``); the flow completes it itself."""
        jc = _JobCtx(job, worker_id, cancel)
        try:
            await self._run_job(jc)
        except CallCancelled:
            await self._retire_job(jc, "cancelled")
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._abort_job(jc)
            raise
        finally:
            self.runs.forget(job.id)

    async def run_next_job(self, worker_id: str = "worker-1") -> bool:
        """Claim the oldest queued job and run it. Returns False when the queue is empty."""
        async with self.store.begin(None) as uow:
            job = await uow.jobs.claim(worker_id)
        if job is None:
            return False
        await self.run_plan_job(job, worker_id, self.runs.token(job.id))
        return True

    async def get_snapshot(self, principal: Principal, session_id: str) -> SessionSnapshot:
        async with self.store.begin(principal.tenant_id) as uow:
            rec, policy = await self._load_readable(uow, principal, session_id)
            gate: GateSnapshot | None = None
            if rec.state == "BRIEF_REVIEW":
                gate = await self._gate_snapshot(uow, rec, policy, "brief")
            elif rec.state == "PLAN_REVIEW":
                gate = await self._gate_snapshot(uow, rec, policy, "plan")
            job = await uow.jobs.active_for_session(session_id)
            last_seq = await self._last_seq(uow, session_id)
        ms = machine.state_of(rec)
        return SessionSnapshot(
            session_id=rec.id,
            app_id=rec.app_id,
            mode=rec.mode,
            policy_id=rec.policy_id,
            state=rec.state,
            plan_phase=rec.plan_phase,
            origin=rec.origin,
            paused_from=rec.paused_from,
            paused_for="budget"
            if rec.state == "PAUSED" and bookkeeping.read_hold(rec.discovery)
            else None,
            language=rec.language,
            pending_card=rec.discovery.pending_card,
            gate=gate,
            brief_version=rec.brief_version,
            approved_brief_version=rec.approved_brief_version,
            research_version=rec.research_version,
            spec_version=rec.spec_version,
            handed_off_version=rec.handed_off_version,
            slots={
                sid: SlotSummary(value=_slot_text(s), state=s.state, source=s.source)
                for sid, s in rec.discovery.slots.items()
            },
            queued_messages=len(rec.queued_messages),
            last_seq=last_seq,
            allowed_transitions=sorted(machine.legal_transitions(ms)),
            active_job=(
                JobSummary(
                    id=job.id,
                    state=job.state,
                    phase=job.phase,
                    cancel_requested=job.cancel_requested,
                )
                if job
                else None
            ),
        )

    @staticmethod
    async def _last_seq(uow: UnitOfWork, session_id: str) -> int:
        """Highest outbound ``seq``, found with O(log n) one-event reads (no log scan)."""

        async def any_after(n: int) -> bool:
            return bool(await uow.events.list_outbound(session_id, after_seq=n, limit=1))

        if not await any_after(0):
            return 0
        lo, hi = 0, 1  # invariant: an event exists after lo; hi is the next probe
        while await any_after(hi):
            lo, hi = hi, hi * 2
        while hi - lo > 1:  # an event exists after lo, none after hi
            mid = (lo + hi) // 2
            if await any_after(mid):
                lo = mid
            else:
                hi = mid
        return hi

    async def get_brief(
        self, principal: Principal, session_id: str, version: int | None = None
    ) -> Brief | None:
        """The current (or a given) Brief version, as shown at Gate 1."""
        async with self.store.begin(principal.tenant_id) as uow:
            await self._load_readable(uow, principal, session_id)
            rec = (
                await uow.briefs.latest(session_id)
                if version is None
                else await uow.briefs.get(session_id, version)
            )
            return rec.brief if rec else None

    async def get_spec(
        self, principal: Principal, session_id: str, version: int | None = None
    ) -> SpecVersionRecord | None:
        async with self.store.begin(principal.tenant_id) as uow:
            rec, _ = await self._load_readable(uow, principal, session_id)
            if version is None:
                # the session's current version, not merely the highest row: a number reserved by
                # a run that never finished is history, not what the user was shown
                return await self._current_spec(uow, rec)
            return await uow.specs.get(session_id, version)

    async def get_plan(
        self, principal: Principal, session_id: str, version: int | None = None
    ) -> PlanView | None:
        """The Plan view of the current (or a given) spec version, with the Enterprise extras
        (approver status, comments, full-file view) filled in from the approvals on that hash."""
        async with self.store.begin(principal.tenant_id) as uow:
            rec, policy = await self._load_readable(uow, principal, session_id)
            spec = (
                await self._current_spec(uow, rec)
                if version is None
                else await uow.specs.get(session_id, version)
            )
            if spec is None or spec.plan is None:
                return None
            approvals = await uow.approvals.list(
                session_id, "plan", version=spec.version, hash=spec.hash
            )
            return gates.plan_with_enterprise_extras(
                spec.plan,
                mode=rec.mode,
                policy=policy,
                approvals=approvals,
                roles=gates.required_roles(policy, "plan"),
            )

    @staticmethod
    async def _current_spec(uow: UnitOfWork, rec: SessionRecord) -> SpecVersionRecord | None:
        if rec.spec_version is None:
            return None
        return await uow.specs.get(rec.id, rec.spec_version)

    async def list_versions(self, principal: Principal, session_id: str) -> list[VersionInfo]:
        async with self.store.begin(principal.tenant_id) as uow:
            await self._load_readable(uow, principal, session_id)
            out: list[VersionInfo] = []
            latest = await uow.briefs.latest(session_id)
            for v in range(1, (latest.brief.version if latest else 0) + 1):
                b = await uow.briefs.get(session_id, v)
                if b is not None:
                    out.append(
                        VersionInfo(
                            kind="brief",
                            version=b.brief.version,
                            hash=b.brief.hash,
                            status=b.brief.status,
                            created_at=b.created_at,
                        )
                    )
            for s in await uow.specs.list_versions(session_id):
                out.append(
                    VersionInfo(
                        kind="spec",
                        version=s.version,
                        hash=s.hash,
                        status=s.status,
                        git_sha=s.git_sha,
                        created_at=s.created_at,
                        approved_at=s.approved_at,
                    )
                )
            return out

    async def list_events(
        self, principal: Principal, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[OutboundEvent]:
        async with self.store.begin(principal.tenant_id) as uow:
            await self._load_readable(uow, principal, session_id)
            return await uow.events.list_outbound(session_id, after_seq=after_seq, limit=limit)

    # ================================================================== plumbing

    def _policy(self, policy_id: str) -> Policy:
        return self.policies(policy_id)

    @staticmethod
    def _ctx_for(session_id: str, tenant_id: str, request_id: str) -> RunContext:
        return RunContext(session_id=session_id, tenant_id=tenant_id, request_id=request_id)

    def _ctx(self, rec: SessionRecord) -> RunContext:
        return self._ctx_for(rec.id, rec.tenant_id, self.ids("req"))

    def _turn(
        self,
        uow: UnitOfWork,
        rec: SessionRecord,
        policy: Policy,
        *,
        reply_to: str | None,
        ctx: RunContext,
        journal: list[_Recorded],
        principal: Principal | None = None,
    ) -> _Turn:
        return _Turn(
            uow=uow,
            rec=rec,
            policy=policy,
            reply_to=reply_to,
            ctx=ctx,
            journal=journal,
            principal=principal,
        )

    @staticmethod
    def _dreq_for(
        rec: SessionRecord,
        policy: Policy,
        *,
        gate_open: GateKind | None = None,
        source: Literal["text", "gate_comment"] = "text",
        org_profile: dict[str, JsonValue] | None = None,
    ) -> DiscoveryRequest:
        return DiscoveryRequest(
            session_id=rec.id,
            tenant_id=rec.tenant_id,
            mode=rec.mode,
            policy=policy,
            state=rec.discovery,
            session_state=rec.state,
            gate_open=gate_open,
            language_hint=rec.language,
            source=source,
            org_profile=org_profile,
        )

    def _dreq(
        self,
        turn: _Turn,
        *,
        gate_open: GateKind | None = None,
        source: Literal["text", "gate_comment"] = "text",
    ) -> DiscoveryRequest:
        return self._dreq_for(turn.rec, turn.policy, gate_open=gate_open, source=source)

    # ------------------------------------------------------------------ engine calls (journalled)

    async def _journalled(
        self, turn: _Turn, name: str, parts: Sequence[object], call: Callable[[], Awaitable[R]]
    ) -> R:
        """An engine call made from inside turn logic. If an earlier pass already ran exactly this
        request, its result is served from the journal; otherwise ``_NeedEngine`` aborts the pass
        so the engine runs with no unit of work open (see ``_run_turn``)."""
        fingerprint = _fingerprint(name, *parts)
        index = turn.cursor
        turn.cursor += 1
        if index < len(turn.journal):
            entry = turn.journal[index]
            if (entry.name, entry.fingerprint) != (name, fingerprint):
                raise _Retry  # the state moved since the engine ran: ask again
            return cast("R", entry.result.model_copy(deep=True))
        raise _NeedEngine(name, fingerprint, call)

    async def _d_handle(
        self, turn: _Turn, req: DiscoveryRequest, event: InboundEvent
    ) -> DiscoveryResult:
        return await self._journalled(
            turn, "handle", (req, event), lambda: self.discovery.handle(req, event, turn.ctx)
        )

    async def _d_apply(
        self, turn: _Turn, req: DiscoveryRequest, ops: list[ChangeOp], source: SlotSource
    ) -> DiscoveryResult:
        return await self._journalled(
            turn,
            "apply_changes",
            (req, ops, source),
            lambda: self.discovery.apply_changes(req, ops, source, turn.ctx),
        )

    async def _d_reopen(self, turn: _Turn, req: DiscoveryRequest, slot_id: str) -> DiscoveryResult:
        return await self._journalled(
            turn,
            "reopen_slot",
            (req, slot_id),
            lambda: self.discovery.reopen_slot(req, slot_id, turn.ctx),
        )

    async def _d_compile(self, turn: _Turn, req: DiscoveryRequest) -> Brief:
        return await self._journalled(
            turn, "compile_brief", (req,), lambda: self.discovery.compile_brief(req, turn.ctx)
        )

    async def _budget_now(self, session_id: str, tenant_id: str) -> BudgetVerdict:
        """Ask the budget gate. A gate that cannot answer never blocks the product: the gateway's
        own guard still refuses every call that would break a cap."""
        gate = self.budget_gate
        if gate is None:
            return _BUDGET_OK
        try:
            return await gate.check(session_id=session_id, tenant_id=tenant_id)
        except Exception:
            log.exception("budget_check_failed", session_id=session_id)
            return _BUDGET_OK

    async def _d_budget(self, turn: _Turn) -> BudgetVerdict:
        """The budget verdict from inside turn logic: journalled like an engine call, so the gate
        (which may be remote) never runs with a unit of work open."""
        if self.budget_gate is None:
            return _BUDGET_OK
        sid, tid = turn.rec.id, turn.rec.tenant_id
        return await self._journalled(
            turn, "budget_check", (sid,), lambda: self._budget_now(sid, tid)
        )

    @staticmethod
    def _adopt_state(rec: SessionRecord, result: DiscoveryResult) -> SessionRecord:
        return rec.model_copy(
            update={"discovery": result.state, "language": result.state.language or rec.language}
        )

    def _adopt_result(
        self,
        turn: _Turn,
        result: DiscoveryResult,
        *,
        before: DiscoveryState | None = None,
        role: str | None = None,
    ) -> DiscoveryResult:
        """Take the engine's state for the session, after settling stakeholder disagreements.

        ``before`` is the state the engine started from (default: the session's), ``role`` the role
        the speaker acted under (an approver's decision), default: every role they hold."""
        result = self._settle(turn, result, before or turn.rec.discovery, role)
        turn.rec = self._adopt_state(turn.rec, result)
        return result

    # ------------------------------------------------------------------ stakeholders (GAP-3)

    @staticmethod
    def _actor(turn: _Turn, role: str | None) -> stakeholders.Actor:
        who = turn.principal
        if who is None:  # a job turn: what was queued came from the owner
            return stakeholders.Actor(user=turn.rec.owner_id, roles=[gates.REQUESTER])
        roles = [role] if role else sorted(gates.held_roles(who, turn.rec.owner_id))
        return stakeholders.Actor(user=who.user_id, roles=roles)

    def _settle(
        self, turn: _Turn, result: DiscoveryResult, before: DiscoveryState, role: str | None
    ) -> DiscoveryResult:
        """Enterprise: when a different person answers a slot someone else already answered, record
        both and resolve by the policy's role precedence or flag the decision owner (spec 9). The
        slots the engine wrote that lose are put back; the rest of the result is untouched."""
        if turn.rec.mode != "enterprise":
            return result
        extra = before.extra
        choices = {
            op.slot_id: value
            for op in result.change_ops
            if op.kind == "set_slot" and op.slot_id and (value := _op_value(op)) is not None
        }
        done = stakeholders.settle_writes(
            before=before.slots,
            after=result.state.slots,
            actor=self._actor(turn, role),
            owner_id=turn.rec.owner_id,
            precedence=turn.policy.role_precedence,
            actors=stakeholders.load_actors(extra.get(bookkeeping.SLOT_ACTORS)),
            conflicts=stakeholders.load_conflicts(extra.get(bookkeeping.SLOT_CONFLICTS)),
            owner_choices=choices,
        )
        state = bookkeeping.with_extra(
            result.state.model_copy(update={"slots": done.slots}),
            **{
                bookkeeping.SLOT_ACTORS: stakeholders.dump_actors(done.actors),
                bookkeeping.SLOT_CONFLICTS: stakeholders.dump_conflicts(done.conflicts),
            },
        )
        for finding in done.findings:
            self._announce(turn, finding)
        turn.settled += len(done.findings)
        if not done.reverted:
            return result.model_copy(update={"state": state})
        return result.model_copy(
            update={
                "state": state,
                "changed_slots": [c for c in result.changed_slots if c not in done.reverted],
                "change_ops": [o for o in result.change_ops if o.slot_id not in done.reverted],
                # "Done, I updated the look" would be untrue for a change that was put back; the
                # notices above say what happened instead
                "events": [e for e in result.events if not isinstance(e, AssistantMessage)],
            }
        )

    def _announce(self, turn: _Turn, f: stakeholders.Finding) -> None:
        """One plain notice per disagreement: both answers, both roles, what was done."""
        slot, kept = _slot_name(f.slot_id), f.kept
        other = f.other or kept
        kept_who, other_who = _roles_text(kept.roles), _roles_text(other.roles)
        if f.kind == "resolved_new":
            self._notice(
                turn,
                messages.N_SLOT_CONFLICT,
                messages.SLOT_CONFLICT_NEW.format(
                    slot=slot,
                    old_value=_show(other.value),
                    old_roles=other_who,
                    new_value=_show(kept.value),
                    new_roles=kept_who,
                ),
            )
        elif f.kind == "resolved_old":
            self._notice(
                turn,
                messages.N_SLOT_CONFLICT,
                messages.SLOT_CONFLICT_OLD.format(
                    slot=slot,
                    old_value=_show(kept.value),
                    old_roles=kept_who,
                    new_value=_show(other.value),
                    new_roles=other_who,
                ),
            )
        elif f.kind == "disputed":
            self._notice(
                turn,
                messages.N_SLOT_DISPUTED,
                messages.SLOT_DISPUTED.format(
                    slot=slot,
                    old_value=_show(kept.value),
                    old_roles=kept_who,
                    new_value=_show(other.value),
                    new_roles=other_who,
                ),
                "warn",
            )
        elif f.kind == "still_disputed":
            self._notice(
                turn,
                messages.N_SLOT_DISPUTED,
                messages.SLOT_STILL_DISPUTED.format(
                    slot=slot, new_value=_show(other.value), new_roles=other_who
                ),
                "warn",
            )
        else:
            self._notice(turn, messages.N_SLOT_SETTLED, messages.SLOT_SETTLED.format(slot=slot))

    @staticmethod
    def _open_disputes(rec: SessionRecord) -> list[str]:
        raw = rec.discovery.extra.get(bookkeeping.SLOT_CONFLICTS)
        return stakeholders.open_disputes(stakeholders.load_conflicts(raw))

    def _authorize(
        self, principal: Principal, rec: SessionRecord, policy: Policy, event: InboundEvent
    ) -> None:
        """The owner may do everything; a required approver may only decide on their gate.

        Only the owner is the ``requester`` (``gates.held_roles``): a ``requester`` in someone's
        claimed roles grants nothing. Anyone else who holds no approver role of this session is told
        it does not exist, exactly like an unknown id (same tenant or not), so a session cannot be
        probed for."""
        if principal.user_id == rec.owner_id:
            return
        self._require_visible(principal, rec, policy)
        held = gates.held_roles(principal, rec.owner_id)
        if isinstance(event, GateDecision):
            if held & set(gates.required_roles(policy, event.gate)):
                return
        elif (
            isinstance(event, UiAction)
            and event.action == "resume"
            and rec.state == "PAUSED"
            and bookkeeping.read_hold(rec.discovery) is not None
        ):
            # the budget is raised by an approver: they, not only the requester, may lift the pause
            approvers = {
                *gates.required_roles(policy, "brief"),
                *gates.required_roles(policy, "plan"),
            }
            if held & approvers:
                return
        raise Forbidden("you cannot act on this session", details={"session_id": rec.id})

    @staticmethod
    def _require_visible(principal: Principal, rec: SessionRecord, policy: Policy) -> None:
        """NotFound unless the caller owns the session or holds a role one of its gates needs."""
        if principal.user_id == rec.owner_id:
            return
        wanted = set(gates.required_roles(policy, "brief")) | set(
            gates.required_roles(policy, "plan")
        )
        if not gates.held_roles(principal, rec.owner_id) & wanted:
            raise NotFound(f"session {rec.id!r} not found")

    async def _load_readable(
        self, uow: UnitOfWork, principal: Principal, session_id: str
    ) -> tuple[SessionRecord, Policy]:
        rec = await uow.sessions.get(session_id)
        if rec is None:
            raise NotFound(f"session {session_id!r} not found")
        policy = self.policies(rec.policy_id)
        self._require_visible(principal, rec, policy)
        return rec, policy

    # ------------------------------------------------------------------ events

    def _new(self, turn: _Turn, event: OutboundEvent) -> OutboundEvent:
        """Stamp a locally-built event with a fresh id, the session, time and reply link."""
        return event.model_copy(
            update={
                "event_id": self.ids("evt"),
                "session_id": turn.rec.id,
                "at": self.clock.now(),
                "in_reply_to": turn.reply_to,
                "seq": 0,
            }
        )

    def _emit(self, turn: _Turn, event: OutboundEvent) -> None:
        turn.out.append(self._new(turn, event))

    def _say(self, turn: _Turn, text: str, *, chips: Sequence[str] = ()) -> None:
        self._emit(
            turn,
            AssistantMessage(
                event_id=_PLACEHOLDER,
                session_id=_PLACEHOLDER,
                text=text,
                language=turn.rec.language,
                chips=list(chips),
                canned=True,
            ),
        )

    def _notice(
        self, turn: _Turn, code: str, text: str, level: Literal["info", "warn"] = "info"
    ) -> None:
        self._emit(
            turn,
            Notice(
                event_id=_PLACEHOLDER, session_id=_PLACEHOLDER, code=code, text=text, level=level
            ),
        )

    def _adopt(self, turn: _Turn, events: Sequence[OutboundEvent], *, cards: bool = True) -> None:
        """Take an engine's replies. Engines may talk and ask; they may NOT emit gates, plan_ready,
        spec_ready or state changes: those belong to the flow controller alone."""
        for ev in events:
            if isinstance(ev, AssistantMessage | Notice | ProgressEvent) or (
                isinstance(ev, CardEvent) and cards
            ):
                self._emit(turn, ev)
            elif not isinstance(ev, CardEvent):
                log.warning("engine_event_dropped", type=ev.type, session_id=turn.rec.id)

    def _transition(
        self,
        turn: _Turn,
        name: FlowTransition,
        *,
        start_phase: PlanPhase | None = None,
        light: bool = False,
    ) -> None:
        """Apply one transition through the machine and announce it."""
        before = turn.rec
        after = machine.apply_to_record(before, name, start_phase=start_phase, light=light)
        turn.rec = after
        self._emit(
            turn,
            StateChanged(
                event_id=_PLACEHOLDER,
                session_id=_PLACEHOLDER,
                from_state=before.state,
                to_state=after.state,
                plan_phase=after.plan_phase,
            ),
        )

    async def _flush(self, turn: _Turn) -> list[OutboundEvent]:
        """Save the session (compare-and-set) and append the outbound events, in order."""
        if turn.rec != turn.original:
            try:
                turn.rec = await turn.uow.sessions.save(
                    turn.rec, expected_version=turn.original.version
                )
            except Conflict:
                raise _Retry from None  # another writer won: re-read and run the turn again
            turn.original = turn.rec
        stored: list[OutboundEvent] = []
        for ev in turn.out:
            stored.append(await turn.uow.events.append_outbound(turn.rec.id, ev))
        turn.out = []
        return stored

    async def _after_commit(self, turn: _Turn, stored: Sequence[OutboundEvent]) -> None:
        """Side effects that must only happen once the unit of work has committed."""
        for action in turn.post:
            await action()
        for ev in stored:
            if isinstance(ev, SpecReady):
                await self._notify_frontend(turn, ev)
        turn.post = []

    async def _notify_frontend(self, turn: _Turn, ev: SpecReady) -> None:
        try:
            await self.frontend.notify(ev)
        except Exception:
            log.exception("frontend_notify_failed", session_id=ev.session_id, version=ev.version)
            async with self.store.begin(turn.rec.tenant_id) as uow:
                note = Notice(
                    event_id=self.ids("evt"),
                    session_id=ev.session_id,
                    at=self.clock.now(),
                    code=messages.N_HANDOFF_NOTIFY_FAILED,
                    text=messages.HANDOFF_NOTIFY_FAILED,
                    level="warn",
                )
                await uow.events.append_outbound(ev.session_id, note)

    # ================================================================== dispatch

    async def _dispatch(self, turn: _Turn, event: InboundEvent) -> None:
        state = turn.rec.state
        if isinstance(event, UiAction) and event.action == "idle":
            # Silence on a pending card: Discover owns the nudge and the assumption (one nudge
            # beside the same card, then the recommended option as ``assumed``; an answer resets
            # it). Only the interview can be silent: at a gate (silence never approves), while
            # planning, paused, archived or handed off it is a no-op that says nothing.
            if state == "DISCOVERING":
                await self._on_discovering(turn, event)
            return
        if state == "ARCHIVED":
            self._notice(turn, messages.N_ARCHIVED, messages.ARCHIVED)
            return
        if isinstance(event, UiAction) and event.action in _CONTROL_ACTIONS:
            await self._on_control_action(turn, event)
            return
        if state == "STOPPED":
            self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)
            return
        if state == "PAUSED":
            self._notice(turn, messages.N_PAUSED, messages.PAUSED)
            return
        if isinstance(event, GateDecision):
            await self._on_gate_decision(turn, event)
        elif state == "DISCOVERING":
            await self._on_discovering(turn, event)
        elif state in ("BRIEF_REVIEW", "PLAN_REVIEW"):
            await self._on_review(turn, event, "brief" if state == "BRIEF_REVIEW" else "plan")
        elif state == "PLANNING":
            await self._on_planning(turn, event)
        else:  # HANDED_OFF
            await self._on_handed_off(turn, event)

    # ------------------------------------------------------------------ control actions

    async def _on_control_action(self, turn: _Turn, event: UiAction) -> None:
        state = turn.rec.state
        if event.action == "stop":
            if state == "PLANNING":
                await self._stop_run(turn)
            else:
                self._notice(turn, messages.N_NOTHING_TO_STOP, messages.NOTHING_TO_STOP)
        elif event.action == "pause":
            await self._pause(turn)
        elif event.action == "resume":
            if state == "PAUSED":
                await self._resume(turn)
            else:
                self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)
        else:  # restart / archive
            await self._archive(turn, restart=event.action == "restart")

    async def _pause(self, turn: _Turn) -> None:
        if turn.rec.state in machine.PAUSABLE:
            self._transition(turn, "pause")
            self._notice(turn, messages.N_PAUSED, messages.PAUSED)
        elif turn.rec.state == "PLANNING":
            self._notice(turn, messages.N_CANNOT_PAUSE, messages.CANNOT_PAUSE)
        else:
            self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)

    async def _cancel_active_run(self, turn: _Turn) -> None:
        outcome = await self.runs.cancel_run(turn.uow, turn.rec.id)
        if outcome.job_id is not None:
            job_id = outcome.job_id
            turn.post.append(lambda: self._cancel_token(job_id))

    async def _cancel_token(self, job_id: str) -> None:
        self.runs.cancel_token(job_id)

    async def _stop_run(self, turn: _Turn) -> None:
        """Stop returns to where the run started and cancels the job."""
        await self._cancel_active_run(turn)
        self._transition(turn, "stop")
        self._say(turn, messages.STOPPED)
        await self._represent(turn)
        await self._drain_queue(turn)

    async def _archive(self, turn: _Turn, *, restart: bool) -> None:
        await self._cancel_active_run(turn)
        self._transition(turn, "archive")
        self._notice(
            turn,
            messages.N_ARCHIVED,
            messages.RESTARTED if restart else messages.ARCHIVED,
        )
        turn.post.append(self._reminder_cancel(turn.rec, "brief"))
        turn.post.append(self._reminder_cancel(turn.rec, "plan"))

    def _reminder_cancel(self, rec: SessionRecord, gate: GateKind) -> PostAction:
        tenant, sid = rec.tenant_id, rec.id

        async def cancel() -> None:
            await self.reminders.cancel(tenant_id=tenant, session_id=sid, gate=gate)

        return cancel

    async def _represent(self, turn: _Turn) -> None:
        """Show where the user is: the pending card or the open gate."""
        state = turn.rec.state
        if state == "DISCOVERING" and turn.rec.discovery.pending_card is not None:
            self._emit(
                turn,
                CardEvent(
                    event_id=_PLACEHOLDER,
                    session_id=_PLACEHOLDER,
                    card=turn.rec.discovery.pending_card,
                ),
            )
        elif state == "BRIEF_REVIEW":
            await self._present_gate(turn, "brief")
        elif state == "PLAN_REVIEW":
            await self._present_gate(turn, "plan")

    # ------------------------------------------------------------------ DISCOVERING

    async def _on_discovering(self, turn: _Turn, event: InboundEvent) -> None:
        waiting = machine.awaiting_answer(machine.state_of(turn.rec))
        question = turn.rec.discovery.pending_card if waiting else None
        event = self._offered_default(turn, event, question)
        result = await self._d_handle(turn, self._dreq(turn), event)
        result = self._adopt_result(turn, result)
        self._adopt(turn, result.events)
        if await self._control_flags(turn, result):
            return
        if waiting:
            await self._after_question_reply(turn, event, result, question)
            return
        if result.brief_complete and result.state.pending_card is None:
            await self._to_brief_review(turn)

    async def _control_flags(self, turn: _Turn, result: DiscoveryResult) -> bool:
        """Typed start over / pause / stop. Returns True when the turn is finished."""
        if result.start_over_requested:
            await self._archive(turn, restart=True)
            return True
        if result.pause_requested:
            await self._pause(turn)
            return True
        if result.stop_requested:
            if turn.rec.state == "PLANNING":
                await self._stop_run(turn)
            else:
                self._notice(turn, messages.N_NOTHING_TO_STOP, messages.NOTHING_TO_STOP)
            return True
        return False

    def _offered_default(
        self, turn: _Turn, event: InboundEvent, question: CardInstance | None
    ) -> InboundEvent:
        """After the "use the default" offer, saying so is a tap on "you decide" for the question."""
        if (
            question is None
            or not isinstance(event, UserMessage)
            or bookkeeping.ANSWER_OFFER not in turn.rec.discovery.extra
            or not _same_words(event.text, messages.CHIP_USE_DEFAULT)
        ):
            return event
        return OptionSelected(
            event_id=self.ids("evt"), card_instance_id=question.instance_id, you_decide=True
        )

    async def _after_question_reply(
        self,
        turn: _Turn,
        event: InboundEvent,
        result: DiscoveryResult,
        question: CardInstance | None,
    ) -> None:
        """A reply while a suspended run's question waits (BUG-6).

        What matters is whether the question was **answered**, not whether a slot changed: a tap, a
        "you decide", a typed reply Discover read as an answer (even with the value the slot already
        had) all settle it, and the run resumes, unless the answer changed the app itself (the run is
        moot and Discover restarts). Chat, a question or off-topic text is not an answer: Discover
        may even drop the card (it does for a card whose slot is already filled), so the flow puts
        the question back, reminds the user, and after a few of those offers the default, so a
        session can never be stuck."""
        pending = result.state.pending_card
        changed = _ops_from_slots(result.changed_slots, result.state.slots, "answer")
        if changed and plan_change(changed).restart_discovery:
            # the reply changed the app itself: the suspended run is moot and the Brief changes
            self._transition(turn, "rediscover")
            self._forget_question(turn)
            if result.brief_complete and pending is None:
                await self._to_brief_review(turn)
            return
        typed = isinstance(event, UserMessage)
        answered = not typed or bool(result.changed_slots) or result.intent in _ANSWER_INTENTS
        if question is not None and not answered:
            self._keep_question(turn, result, question)
            return
        if pending is not None:
            if question is not None and pending.card.id == question.card.id:
                self._keep_question(turn, result, question)  # asked again: still not settled
            return  # otherwise the engine has follow-up questions first
        slots: list[str] = list(result.changed_slots)
        if question is not None and question.card.slot not in slots:
            slots.append(question.card.slot)
        await self._resume_planning(
            turn, _ops_from_slots(slots, result.state.slots, "answer"), slots
        )

    def _keep_question(self, turn: _Turn, result: DiscoveryResult, question: CardInstance) -> None:
        """The reply was not the answer: the question stays (shown again), with a reminder; the
        third such reply in a row also offers "use the default" (a typed "Use the default" then
        means "you decide" for the question, see ``_offered_default``)."""
        state = turn.rec.discovery
        if state.pending_card is None:
            state = state.model_copy(update={"pending_card": question})
        if not any(
            isinstance(e, CardEvent) and e.card.instance_id == question.instance_id
            for e in result.events
        ):
            self._emit(
                turn, CardEvent(event_id=_PLACEHOLDER, session_id=_PLACEHOLDER, card=question)
            )
        tries = bookkeeping.count(state.extra, bookkeeping.ANSWER_TRIES)
        if not result.changed_slots:  # an edit of another slot is progress, chatter is not
            tries += 1
        state = bookkeeping.with_extra(state, **{bookkeeping.ANSWER_TRIES: str(tries)})
        if tries >= MAX_UNANSWERED_REPLIES and question.card.skip.enabled:
            state = bookkeeping.with_extra(state, **{bookkeeping.ANSWER_OFFER: "1"})
            self._say(turn, messages.ANSWER_OFFER_DEFAULT, chips=[messages.CHIP_USE_DEFAULT])
        else:
            self._notice(turn, messages.N_ANSWER_NEEDED, messages.ANSWER_NEEDED)
        turn.rec = turn.rec.model_copy(update={"discovery": state})

    @staticmethod
    def _forget_question(turn: _Turn) -> None:
        """The suspended run's question is over: drop the reminder counters."""
        state = bookkeeping.without_flow_keys(
            turn.rec.discovery, bookkeeping.ANSWER_TRIES, bookkeeping.ANSWER_OFFER
        )
        turn.rec = turn.rec.model_copy(update={"discovery": state})

    async def _to_brief_review(self, turn: _Turn) -> None:
        self._transition(turn, "brief_complete")
        await self._compile_and_open_brief(turn)

    async def _compile_and_open_brief(self, turn: _Turn) -> None:
        """Compile a new Brief version from the slots and open Gate 1 (with a diff if it re-opens)."""
        uow, rec = turn.uow, turn.rec
        prev = await uow.briefs.latest(rec.id)
        brief = await self._d_compile(turn, self._dreq(turn))
        version = (prev.brief.version if prev else 0) + 1
        brief = brief.model_copy(
            update={
                "version": version,
                "status": "draft",
                "mode": rec.mode,
                "language": rec.language,
                "created_at": self.clock.now(),
            }
        ).with_hash()
        await uow.briefs.add(
            BriefVersionRecord(
                session_id=rec.id,
                tenant_id=rec.tenant_id,
                brief=brief,
                slots=dict(rec.discovery.slots),
            )
        )
        voided = False
        if prev is not None:
            if prev.brief.status != "superseded":
                await uow.briefs.set_status(rec.id, prev.brief.version, "superseded")
            voided = bool(
                await uow.approvals.list(
                    rec.id, "brief", version=prev.brief.version, hash=prev.brief.hash
                )
            )
        turn.rec = rec.model_copy(update={"brief_version": version})
        lines = brief_diff(prev.brief, brief) if prev is not None else []
        if voided:
            self._say(turn, messages.BRIEF_CHANGED + " " + messages.APPROVAL_VOIDED)
        await self._present_gate(turn, "brief", diff_lines=lines)

    # ------------------------------------------------------------------ gate presentation

    async def _current_artifact(self, turn: _Turn, gate: GateKind) -> tuple[int, str] | None:
        rec, uow = turn.rec, turn.uow
        if gate == "brief":
            if rec.brief_version is None:
                return None
            b = await uow.briefs.get(rec.id, rec.brief_version)
            return (b.brief.version, b.brief.hash) if b else None
        if rec.spec_version is None:
            return None
        s = await uow.specs.get(rec.id, rec.spec_version)
        return (s.version, s.hash) if s else None

    async def _present_gate(
        self, turn: _Turn, gate: GateKind, *, diff_lines: Sequence[str] = ()
    ) -> GateStatus:
        rec, uow, policy = turn.rec, turn.uow, turn.policy
        roles = gates.required_roles(policy, gate)
        if gate == "brief":
            assert rec.brief_version is not None
            brief_rec = await uow.briefs.get(rec.id, rec.brief_version)
            if brief_rec is None:
                raise NotFound("the current Brief version is missing")
            version, digest = brief_rec.brief.version, brief_rec.brief.hash
            approvals = await uow.approvals.list(rec.id, "brief", version=version, hash=digest)
            page: BriefPage | PlanPage = gates.brief_page(brief_rec.brief)
        else:
            assert rec.spec_version is not None
            spec_rec = await uow.specs.get(rec.id, rec.spec_version)
            if spec_rec is None or spec_rec.plan is None:
                raise NotFound("the current Plan version is missing")
            version, digest = spec_rec.version, spec_rec.hash
            approvals = await uow.approvals.list(rec.id, "plan", version=version, hash=digest)
            plan = gates.plan_with_enterprise_extras(
                spec_rec.plan, mode=rec.mode, policy=policy, approvals=approvals, roles=roles
            )
            page = gates.plan_page(plan)
        event, status = gates.open_gate(
            gate=gate,
            version=version,
            hash=digest,
            page=page,
            mode=rec.mode,
            roles=roles,
            approvals=approvals,
            session_id=rec.id,
            event_id=self.ids("evt"),
            at=self.clock.now(),
            diff_lines=diff_lines,
            in_reply_to=turn.reply_to,
        )
        turn.out.append(event)
        hours = gates.reminder_hours(policy, gate)
        if hours is not None and rec.mode == "enterprise" and status.status != "approved":
            tenant, sid = rec.tenant_id, rec.id
            pending = [r for r in roles if r not in event.approved_roles]
            turn.post.append(
                lambda: self.reminders.schedule(
                    tenant_id=tenant,
                    session_id=sid,
                    gate=gate,
                    version=version,
                    hash=digest,
                    roles=pending,
                    after_hours=hours,
                )
            )
        return status

    async def _gate_snapshot(
        self, uow: UnitOfWork, rec: SessionRecord, policy: Policy, gate: GateKind
    ) -> GateSnapshot | None:
        roles = gates.required_roles(policy, gate)
        if gate == "brief" and rec.brief_version is not None:
            b = await uow.briefs.get(rec.id, rec.brief_version)
            if b is None:
                return None
            approvals = await uow.approvals.list(
                rec.id, "brief", version=b.brief.version, hash=b.brief.hash
            )
            status = gates.derive_status(
                gate="brief", version=b.brief.version, hash=b.brief.hash,
                approvals=approvals, roles=roles,
            )  # fmt: skip
            return GateSnapshot(
                gate="brief",
                version=b.brief.version,
                hash=b.brief.hash,
                status=status,
                buttons=gates.gate_buttons(rec.mode, single_button=False),
                page=gates.brief_page(b.brief),
            )
        if gate == "plan" and rec.spec_version is not None:
            s = await uow.specs.get(rec.id, rec.spec_version)
            if s is None or s.plan is None:
                return None
            approvals = await uow.approvals.list(rec.id, "plan", version=s.version, hash=s.hash)
            status = gates.derive_status(
                gate="plan", version=s.version, hash=s.hash, approvals=approvals, roles=roles
            )
            plan = gates.plan_with_enterprise_extras(
                s.plan, mode=rec.mode, policy=policy, approvals=approvals, roles=roles
            )
            return GateSnapshot(
                gate="plan",
                version=s.version,
                hash=s.hash,
                status=status,
                buttons=gates.gate_buttons(rec.mode, single_button=False),
                page=gates.plan_page(plan),
            )
        return None

    # ------------------------------------------------------------------ gates: decisions

    async def _on_gate_decision(self, turn: _Turn, event: GateDecision) -> None:
        await self._decide(
            turn,
            event.gate,
            event.decision,
            via="tap",
            version=event.version,
            hash=event.hash,
            comment=event.comment,
            role=event.role,
            approver_id=event.approver_id,
        )

    async def _decide(
        self,
        turn: _Turn,
        gate: GateKind,
        decision: Literal["approve", "change", "reject"],
        *,
        via: ApprovalVia,
        version: int | None = None,
        hash: str | None = None,
        comment: str | None = None,
        role: str | None = None,
        approver_id: str | None = None,
    ) -> None:
        rec, uow, policy = turn.rec, turn.uow, turn.policy
        acting = turn.principal
        if acting is None:  # pragma: no cover - decisions always come from a caller
            raise GateError("a decision needs an authenticated caller")
        expected = "BRIEF_REVIEW" if gate == "brief" else "PLAN_REVIEW"
        current = await self._current_artifact(turn, gate) if rec.state == expected else None
        if current is None:
            self._notice(turn, messages.N_GATE_CLOSED, messages.GATE_CLOSED)
            return
        cur_version, cur_hash = current
        roles = gates.required_roles(policy, gate)
        existing = await uow.approvals.list(rec.id, gate, version=cur_version, hash=cur_hash)
        try:
            outcome = gates.record_decision(
                session_id=rec.id,
                gate=gate,
                gate_open=True,
                current_version=cur_version,
                current_hash=cur_hash,
                decision=decision,
                version=cur_version if version is None else version,
                hash=cur_hash if hash is None else hash,
                via=via,
                principal=acting,
                owner_id=rec.owner_id,
                mode=rec.mode,
                roles=roles,
                existing=existing,
                new_id=self.ids,
                at=self.clock.now(),
                comment=comment,
                requested_role=role,
                approver_id=approver_id,
            )
        except HashMismatch:
            await self._show_stale(turn, gate, version, hash)
            return
        if outcome.record is not None:
            await uow.approvals.add(outcome.record)
        kind = outcome.kind
        disputed = self._open_disputes(turn.rec)
        if kind in ("approved", "duplicate") and outcome.complete and disputed:
            # every approver said yes, but two stakeholders still disagree about the content: the
            # decision owner settles that first (nothing is overwritten silently, spec 9)
            self._notice(
                turn,
                messages.N_DISPUTE_BLOCKS,
                messages.DISPUTE_BLOCKS.format(slots=", ".join(_slot_name(d) for d in disputed)),
                "warn",
            )
            await self._present_gate(turn, gate)
        elif kind in ("approved", "duplicate") and outcome.complete:
            await self._gate_complete(turn, gate)
        elif kind in ("pending", "duplicate"):
            done = gates.approved_roles(
                [*existing, *([outcome.record] if outcome.record else [])],
                version=cur_version,
                hash=cur_hash,
            )
            missing = [r for r in roles if r not in done]
            if missing:
                self._notice(
                    turn, messages.N_WAITING_APPROVERS,
                    messages.WAITING_APPROVERS.format(roles=", ".join(missing)),
                )  # fmt: skip
            await self._present_gate(turn, gate)
        elif kind == "submitted":
            self._notice(
                turn, messages.N_SUBMITTED, messages.SUBMITTED.format(roles=", ".join(roles))
            )
            await self._present_gate(turn, gate)
        else:  # change_requested / rejected
            await self._on_comments(
                turn, gate, outcome.comments, rejected=kind == "rejected", role=outcome.role
            )

    async def _show_stale(
        self, turn: _Turn, gate: GateKind, version: int | None, digest: str | None
    ) -> None:
        """An answer for an old version or hash: do not count it, show the current page + a diff."""
        self._notice(turn, messages.N_STALE_GATE, messages.STALE_GATE)
        lines: list[str] = []
        uow, rec = turn.uow, turn.rec
        if version is not None and version > 0:
            if gate == "brief" and rec.brief_version is not None:
                old = await uow.briefs.get(rec.id, version)
                cur = await uow.briefs.get(rec.id, rec.brief_version)
                if old and cur and old.brief.hash != cur.brief.hash:
                    lines = brief_diff(old.brief, cur.brief)
            elif gate == "plan" and rec.spec_version is not None:
                old_s = await uow.specs.get(rec.id, version)
                cur_s = await uow.specs.get(rec.id, rec.spec_version)
                if old_s and cur_s and old_s.hash != cur_s.hash:
                    lines = plan_diff(old_s.spec, cur_s.spec).lines
        await self._present_gate(turn, gate, diff_lines=lines)

    async def _on_comments(
        self,
        turn: _Turn,
        gate: GateKind,
        comments: Sequence[str],
        *,
        rejected: bool,
        role: str | None = None,
    ) -> None:
        """A rejection's comments (or a 'Change something' note) become change requests."""
        if not comments:
            if rejected:
                self._notice(turn, messages.N_NOT_UNDERSTOOD, messages.NOT_UNDERSTOOD)
            else:
                self._say(turn, messages.GATE_CHANGE_PROMPT)
            await self._present_gate(turn, gate)
            return
        combined = await self._interpret_texts(
            turn, comments, gate_open=gate, source="gate_comment", role=role
        )
        if combined is None:
            if not turn.settled:  # a disagreement was announced: that is the answer
                self._notice(turn, messages.N_NOT_UNDERSTOOD, messages.NOT_UNDERSTOOD)
            await self._present_gate(turn, gate)
            return
        await self._apply_change_result(turn, combined)

    async def _interpret_texts(
        self,
        turn: _Turn,
        texts: Sequence[str],
        *,
        gate_open: GateKind | None,
        source: Literal["text", "gate_comment"] = "text",
        role: str | None = None,
    ) -> DiscoveryResult | None:
        """Run each text through the discovery engine in turn; combine the changes found.

        ``source`` tells the engine what the text is: typed ``text``, or a ``gate_comment`` (the
        comment of a "Change something" / rejection decision, which is a change request).
        Returns None when no text produced a change. The engine's slot updates are adopted. The
        combined result's ``intent`` is ``change`` only when **every** text that changed something
        was an explicit change request; otherwise the change is an inference (see ``_on_handed_off``).
        """
        state = turn.rec.discovery
        ops: list[ChangeOp] = []
        changed: list[str] = []
        asked = True
        last: DiscoveryResult | None = None
        for text in texts:
            req = self._dreq(turn, gate_open=gate_open, source=source).model_copy(
                update={"state": state}
            )
            msg = UserMessage(event_id=self.ids("evt"), text=text)
            res = self._settle(turn, await self._d_handle(turn, req, msg), state, role)
            state = res.state
            ops.extend(res.change_ops)
            changed.extend(c for c in res.changed_slots if c not in changed)
            if (res.change_ops or res.changed_slots) and res.intent != "change":
                asked = False
            self._adopt(turn, res.events, cards=False)
            last = res
        if last is None or not (ops or changed):
            if last is not None:
                turn.rec = self._adopt_state(turn.rec, last)
            return None
        turn.rec = self._adopt_state(turn.rec, last)
        return last.model_copy(
            update={
                "change_ops": ops,
                "changed_slots": changed,
                "state": state,
                "intent": "change"
                if asked
                else (last.intent if last.intent != "change" else "other"),
            }
        )

    # ------------------------------------------------------------------ gates: completion

    async def _gate_complete(self, turn: _Turn, gate: GateKind) -> None:
        if gate == "brief":
            await self._approve_brief(turn)
        else:
            await self._approve_plan(turn)

    @staticmethod
    async def _has_research(uow: UnitOfWork, rec: SessionRecord) -> bool:
        """Research already ran for the Brief that is being approved (and did not fail)."""
        assert rec.brief_version is not None
        note = await uow.research.latest(rec.id)
        brief = await uow.briefs.get(rec.id, rec.brief_version)
        return (
            note is not None
            and brief is not None
            and note.note.status != "unavailable"
            and note.note.brief_hash == brief.brief.hash
        )

    async def _gate_is_complete(self, turn: _Turn, gate: GateKind) -> bool:
        """Every required role approved the gate's current version and hash."""
        current = await self._current_artifact(turn, gate)
        if current is None:
            return False
        version, digest = current
        approvals = await turn.uow.approvals.list(turn.rec.id, gate, version=version, hash=digest)
        roles = gates.required_roles(turn.policy, gate)
        return gates.is_complete(roles, approvals, version=version, hash=digest)

    # ------------------------------------------------------------------ the budget pause (GAP-5)

    def _budget_notice(self, turn: _Turn, verdict: BudgetVerdict, *, dropped: bool = False) -> None:
        """Plain words, never a number: Guided says it briefly, Enterprise names the approvers."""
        if turn.rec.mode == "guided":
            text = messages.BUDGET_PAUSED_GUIDED
        else:
            text = verdict.message or messages.BUDGET_REACHED
            wanted = {
                *gates.required_roles(turn.policy, "brief"),
                *gates.required_roles(turn.policy, "plan"),
            }
            approvers = sorted(wanted - {gates.REQUESTER})
            if approvers:
                text += messages.BUDGET_APPROVERS.format(roles=", ".join(approvers))
        if dropped:
            text += messages.BUDGET_CHANGE_DROPPED
        self._notice(turn, messages.N_BUDGET_CAP, text, "warn")

    def _hold_for_budget(self, turn: _Turn, verdict: BudgetVerdict, hold: BudgetHold) -> None:
        """Do not start (or continue) the run: park the session in PAUSED, say why, and remember
        what to start again when an approver lifts the pause with ``resume``."""
        if turn.rec.state in machine.PAUSABLE:
            self._transition(turn, "pause")
            state = bookkeeping.set_hold(turn.rec.discovery, hold)
            turn.rec = turn.rec.model_copy(update={"discovery": state})
        self._budget_notice(turn, verdict, dropped=hold.kind == "dropped")

    async def _resume(self, turn: _Turn) -> None:
        """Lift a pause. A budget pause is only lifted when the budget allows planning again, and
        then what was held (the run Gate 1 approved, a builder question's answer) starts."""
        hold = bookkeeping.read_hold(turn.rec.discovery)
        if hold is None:
            self._transition(turn, "resume")
            await self._represent(turn)
            return
        verdict = await self._d_budget(turn)
        if not verdict.ok:
            self._budget_notice(turn, verdict)
            return
        self._transition(turn, "resume")
        turn.rec = turn.rec.model_copy(
            update={"discovery": bookkeeping.clear_hold(turn.rec.discovery)}
        )
        if hold.kind == "gate1" and turn.rec.state == "BRIEF_REVIEW":
            if await self._gate_is_complete(turn, "brief"):
                await self._approve_brief(turn, verdict, reuse_research=True)
                return
        elif hold.kind == "answer" and machine.awaiting_answer(machine.state_of(turn.rec)):
            answers = _ops_from_slots(hold.answers, turn.rec.discovery.slots, "answer")
            await self._resume_planning(turn, answers, hold.answers, verdict)
            return
        await self._represent(turn)

    async def _next_spec_version(self, uow: UnitOfWork, session_id: str) -> int:
        latest = await uow.specs.latest(session_id)
        return (latest.version if latest else 0) + 1

    async def _approve_brief(
        self,
        turn: _Turn,
        verdict: BudgetVerdict | None = None,
        *,
        reuse_research: bool = False,
    ) -> None:
        """Gate 1 is complete on the current hash: start the run (research, then build), unless
        the budget cap is reached: then nothing starts and the session pauses (spec 9).

        ``reuse_research``: the run was halted for the budget after research had finished; starting
        it again must not pay for the same research twice (an empty ``rerun_jobs`` hands the engine
        its previous note back)."""
        uow, rec = turn.uow, turn.rec
        assert rec.brief_version is not None
        verdict = verdict or await self._d_budget(turn)
        if not verdict.ok:
            self._hold_for_budget(turn, verdict, BudgetHold(kind="gate1"))
            return
        version = rec.brief_version
        payload = PlanJobPayload(
            origin="BRIEF_REVIEW",
            start_phase="research",
            spec_version=await self._next_spec_version(uow, rec.id),
            brief_version=version,
            rerun_jobs=[] if reuse_research and await self._has_research(uow, rec) else None,
        )
        try:
            await self.runs.start_run(
                uow, session_id=rec.id, tenant_id=rec.tenant_id, payload=payload
            )
        except Conflict:
            self._notice(turn, messages.N_RUN_STOPPING, messages.RUN_STOPPING, "warn")
            return
        await uow.briefs.set_status(rec.id, version, "approved")
        if rec.approved_brief_version not in (None, version):
            assert rec.approved_brief_version is not None
            await uow.briefs.set_status(rec.id, rec.approved_brief_version, "superseded")
        turn.rec = rec.model_copy(update={"approved_brief_version": version})
        self._transition(turn, "approve_brief")
        self._say(turn, messages.STARTING_PLAN)
        turn.post.append(self._reminder_cancel(turn.rec, "brief"))

    async def _approve_plan(self, turn: _Turn) -> None:
        """Gate 2 is complete on the current hash: hand off (commit, then spec_ready)."""
        uow, rec, policy = turn.uow, turn.rec, turn.policy
        assert rec.spec_version is not None
        spec = await uow.specs.get(rec.id, rec.spec_version)
        if spec is None:
            raise NotFound("the current spec version is missing")
        approvals = await uow.approvals.list(rec.id, "plan", version=spec.version, hash=spec.hash)
        gates.assert_handoff_allowed(
            version=spec.version,
            spec_hash=spec.hash,
            spec_text=spec.spec_text,
            roles=gates.required_roles(policy, "plan"),
            approvals=approvals,
        )
        await self._handoff(turn, spec, transition="approve_plan", light=False)
        turn.post.append(self._reminder_cancel(turn.rec, "plan"))

    async def _handoff(
        self,
        turn: _Turn,
        spec: SpecVersionRecord,
        *,
        transition: Literal["approve_plan", "plan_ready"],
        light: bool,
    ) -> None:
        """The ONLY place ``spec_ready`` is created. Callers must have passed
        ``gates.assert_handoff_allowed`` for exactly this spec version and hash."""
        uow, rec = turn.uow, turn.rec
        await uow.specs.set_status(rec.id, spec.version, "approved", approved_at=self.clock.now())
        try:
            info = await self.project_repo.commit_spec(
                tenant_id=rec.tenant_id,
                app_id=rec.app_id,
                spec_text=spec.spec_text,
                version=spec.version,
                spec_hash=spec.hash,
                message=f"Approve spec v{spec.version}",
            )
        except (Conflict, HandoffFailed):
            raise
        except Exception as exc:
            raise HandoffFailed("could not save the approved spec") from exc
        await uow.specs.set_status(rec.id, spec.version, "handed_off", git_sha=info.sha)
        for other in await uow.specs.list_versions(rec.id):
            if other.version != spec.version and other.status in ("draft", "approved"):
                await uow.specs.set_status(rec.id, other.version, "superseded")
        self._transition(turn, transition, light=light)
        turn.rec = turn.rec.model_copy(update={"handed_off_version": spec.version})
        self._emit(
            turn,
            SpecReady(
                event_id=_PLACEHOLDER,
                session_id=_PLACEHOLDER,
                app_id=rec.app_id,
                version=spec.version,
                hash=spec.hash,
                git_sha=info.sha,
                path=info.path,
                repo_dir=info.repo_dir,
            ),
        )

    # ------------------------------------------------------------------ BRIEF_REVIEW / PLAN_REVIEW

    async def _on_review(self, turn: _Turn, event: InboundEvent, gate: GateKind) -> None:
        if isinstance(event, UiAction):
            await self._on_review_action(turn, event, gate)
            return
        result = await self._d_handle(turn, self._dreq(turn, gate_open=gate), event)
        result = self._adopt_result(turn, result)
        if await self._control_flags(turn, result):
            self._adopt(turn, result.events)
            return
        if gates.typed_approval_counts(gate_open=gate, result=result):
            self._adopt(turn, result.events)
            await self._decide(turn, gate, "approve", via="typed")
            return
        if result.change_ops or result.changed_slots:
            self._adopt(turn, result.events)
            await self._apply_change_result(turn, result)
            return
        self._adopt(turn, result.events, cards=False)
        if result.intent == "approve":
            self._notice(turn, messages.N_NOT_AN_APPROVAL, messages.NOT_AN_APPROVAL)
        await self._present_gate(turn, gate)

    async def _on_review_action(self, turn: _Turn, event: UiAction, gate: GateKind) -> None:
        action = event.action
        if action == "edit_row" and gate == "brief":
            slot_id = await self._row_slot(turn, event.target)
            if slot_id is None:
                self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)
                return
            result = await self._d_reopen(turn, self._dreq(turn, gate_open=gate), slot_id)
            self._transition(turn, "change_brief")
            self._adopt_result(turn, result)
            self._adopt(turn, result.events)
            return
        ops = _ops_from_action(event)
        if ops is None or (action == "toggle_research_item" and gate != "plan"):
            self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)
            return
        if action == "toggle_research_item":
            await self._start_change_run(turn, ops, via="tap", discovery_complete=True)
            return
        result = await self._d_apply(turn, self._dreq(turn, gate_open=gate), ops, "tap")
        result = self._adopt_result(turn, result.model_copy(update={"change_ops": ops}))
        self._adopt(turn, result.events)
        await self._apply_change_result(turn, result, via="tap")

    async def _row_slot(self, turn: _Turn, target: str | None) -> str | None:
        """The slot a Brief row edits: ``target`` is a slot id or a row key."""
        if not target:
            return None
        if "." in target:
            return target if target in SLOT_IDS else None
        if turn.rec.brief_version is None:
            return None
        rec = await turn.uow.briefs.get(turn.rec.id, turn.rec.brief_version)
        row: BriefRow | None = rec.brief.row(target) if rec else None  # type: ignore[arg-type]
        return row.slot_ids[0] if row and row.slot_ids else None

    async def _apply_change_result(
        self,
        turn: _Turn,
        result: DiscoveryResult,
        *,
        via: ApprovalVia = "typed",
        derived: bool = False,
    ) -> None:
        """A change the user asked for is its own approval of that change (rule 4): apply it now.

        ``derived`` marks a change that came from an uploaded document or an engine inference rather
        than from an explicit instruction or tap: after hand-off it is never light."""
        state = turn.rec.state
        if state == "BRIEF_REVIEW":
            await self._brief_changed(turn, result)
        elif state in ("PLAN_REVIEW", "HANDED_OFF"):
            ops = list(result.change_ops) or _ops_from_slots(
                result.changed_slots, result.state.slots
            )
            await self._start_change_run(
                turn,
                ops,
                via=via,
                discovery_complete=result.brief_complete and result.state.pending_card is None,
                derived=derived,
            )

    async def _brief_changed(self, turn: _Turn, result: DiscoveryResult) -> None:
        """Gate 1 change: re-enter the loop for the affected slot, then return at once."""
        self._transition(turn, "change_brief")
        if result.brief_complete and result.state.pending_card is None:
            await self._to_brief_review(turn)

    # ------------------------------------------------------------------ change runs

    def _note_stale(self, rec: ResearchNoteRecord | None) -> bool:
        if rec is None or rec.note.status == "unavailable" or rec.created_at is None:
            return False
        return self.clock.now() - rec.created_at > timedelta(days=RESEARCH_STALE_DAYS)

    @staticmethod
    def _excluded_ids(prev: SpecVersionRecord | None, ops: Sequence[ChangeOp]) -> list[str]:
        excluded = (
            {a.id for a in prev.spec.assumptions.research_additions if not a.included}
            if prev
            else set[str]()
        )
        for op in ops:
            if op.kind == "toggle_research_item" and op.target_id:
                if op.value_bool is None:
                    excluded ^= {op.target_id}
                elif op.value_bool:
                    excluded.discard(op.target_id)
                else:
                    excluded.add(op.target_id)
        return sorted(excluded)

    async def _start_change_run(
        self,
        turn: _Turn,
        ops: Sequence[ChangeOp],
        *,
        via: ApprovalVia,
        discovery_complete: bool,
        derived: bool = False,
    ) -> None:
        """A change at Gate 2 or after hand-off: patch, only the affected writers re-run."""
        uow, rec = turn.uow, turn.rec
        note_rec = await uow.research.latest(rec.id)
        plan = plan_change(
            ops, note=note_rec.note if note_rec else None, research_stale=self._note_stale(note_rec)
        )
        if not plan.needs_rebuild:
            self._notice(turn, messages.N_NO_CHANGE, messages.NO_CHANGE)
            return
        from_state = rec.state
        name: FlowTransition = (
            "change_plan" if from_state == "PLAN_REVIEW" else "change_after_handoff"
        )
        if plan.restart_discovery:
            # app type / scope.apps / region: the whole Brief changes, so back to Discover first
            self._transition(turn, name, start_phase="build")
            self._transition(turn, "rediscover")
            self._say(turn, messages.RUN_CANCELLED_REDISCOVER)
            if discovery_complete:
                await self._to_brief_review(turn)
            return
        verdict = await self._d_budget(turn)
        if not verdict.ok:
            # the change is not started (nothing was built for it); the user asks again after resume
            self._hold_for_budget(turn, verdict, BudgetHold(kind="dropped"))
            return
        prev = await uow.specs.latest(rec.id)
        origin: RunOrigin = "PLAN_REVIEW" if from_state == "PLAN_REVIEW" else "HANDED_OFF"
        assert rec.approved_brief_version is not None
        payload = PlanJobPayload(
            origin=origin,
            start_phase=plan.start_phase,
            spec_version=await self._next_spec_version(uow, rec.id),
            brief_version=rec.approved_brief_version,
            affected_sections=list(plan.sections),
            rerun_jobs=list(plan.rerun_jobs) or None,
            excluded_research_ids=self._excluded_ids(prev, ops),
            change_ops_json=encode_intent(JobIntent(ops=list(ops), via=via, derived=derived)),
        )
        try:
            await self.runs.start_run(
                uow, session_id=rec.id, tenant_id=rec.tenant_id, payload=payload
            )
        except Conflict:
            self._notice(turn, messages.N_RUN_STOPPING, messages.RUN_STOPPING, "warn")
            return
        self._transition(turn, name, start_phase=plan.start_phase)
        self._say(turn, messages.RESUMING_PLAN)
        if from_state == "PLAN_REVIEW":
            turn.post.append(self._reminder_cancel(turn.rec, "plan"))

    async def _resume_planning(
        self,
        turn: _Turn,
        answers: Sequence[ChangeOp],
        slot_ids: Sequence[str],
        verdict: BudgetVerdict | None = None,
    ) -> None:
        """A ``needs_answer`` question was answered: re-run the failing writer (the budget allowing).

        ``slot_ids`` are the slots the reply settled, the question's own slot included even when the
        reply left its value as it was."""
        uow, rec = turn.uow, turn.rec
        assert rec.approved_brief_version is not None
        verdict = verdict or await self._d_budget(turn)
        if not verdict.ok:
            self._forget_question(turn)
            self._hold_for_budget(turn, verdict, BudgetHold(kind="answer", answers=list(slot_ids)))
            return
        origin: RunOrigin = rec.origin or "BRIEF_REVIEW"
        phase: PlanPhase = rec.plan_phase or "build"
        payload = PlanJobPayload(
            origin=origin,
            start_phase=phase,
            spec_version=await self._next_spec_version(uow, rec.id),
            brief_version=rec.approved_brief_version,
            answer_slot_ids=list(slot_ids),
            change_ops_json=encode_intent(JobIntent(answers=list(answers), via="typed")),
        )
        try:
            await self.runs.start_run(
                uow, session_id=rec.id, tenant_id=rec.tenant_id, payload=payload
            )
        except Conflict:
            self._notice(turn, messages.N_RUN_STOPPING, messages.RUN_STOPPING, "warn")
            return
        self._transition(turn, "answer_given")
        self._forget_question(turn)
        self._say(turn, messages.RESUMING_PLAN)

    # ------------------------------------------------------------------ PLANNING

    async def _on_planning(self, turn: _Turn, event: InboundEvent) -> None:
        if not isinstance(event, UserMessage):
            self._notice(turn, messages.N_WAIT_FOR_PLAN, messages.WAIT_FOR_PLAN)
            return
        result = await self._d_handle(turn, self._dreq(turn), event)
        if await self._control_flags(turn, result):
            return
        ops = list(result.change_ops) or _ops_from_slots(result.changed_slots, result.state.slots)
        change = plan_change(ops) if ops else None
        if change is not None and change.restart_discovery:
            await self._cancel_active_run(turn)
            turn.rec = self._adopt_state(turn.rec, result)
            self._transition(turn, "rediscover")
            self._adopt(turn, result.events)
            self._say(turn, messages.RUN_CANCELLED_REDISCOVER)
            if result.brief_complete and result.state.pending_card is None:
                await self._to_brief_review(turn)
            return
        job = await turn.uow.jobs.active_for_session(turn.rec.id)
        if ops and job is not None and job.state == "queued":
            # approve, then immediately change: nothing has run yet, so the change arrives at the
            # gate it came from: the version changes, the approval is void, the gate re-opens.
            await self._cancel_active_run(turn)
            self._transition(turn, "stop")
            await self._dispatch(turn, event)
            return
        rec, accepted = self.runs.queue_message(turn.rec, event.text)
        turn.rec = rec
        if accepted:
            self._say(turn, messages.QUEUED)
        else:
            self._notice(turn, messages.N_QUEUED, messages.WAIT_FOR_PLAN, "warn")

    async def _drain_queue(self, turn: _Turn) -> None:
        """Apply the messages typed during PLANNING now that a gate (or hand-off) is reached."""
        texts = list(turn.rec.queued_messages)
        if not texts or turn.rec.state not in ("BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF"):
            return
        turn.rec = turn.rec.model_copy(update={"queued_messages": []})
        self._say(turn, messages.APPLYING_QUEUED)
        gate: GateKind | None = (
            "brief"
            if turn.rec.state == "BRIEF_REVIEW"
            else "plan"
            if turn.rec.state == "PLAN_REVIEW"
            else None
        )
        combined = await self._interpret_texts(turn, texts, gate_open=gate)
        if combined is not None:
            await self._apply_change_result(turn, combined, derived=combined.intent != "change")

    # ------------------------------------------------------------------ HANDED_OFF

    async def _on_handed_off(self, turn: _Turn, event: InboundEvent) -> None:
        if isinstance(event, UiAction):
            ops = _ops_from_action(event)
            if ops is None:
                self._notice(turn, messages.N_UNSUPPORTED_ACTION, messages.UNSUPPORTED_ACTION)
                return
            if event.action == "toggle_research_item":
                await self._start_change_run(turn, ops, via="tap", discovery_complete=True)
                return
            result = await self._d_apply(turn, self._dreq(turn), ops, "tap")
            result = self._adopt_result(turn, result.model_copy(update={"change_ops": ops}))
            self._adopt(turn, result.events)
            await self._apply_change_result(turn, result, via="tap")
            return
        result = await self._d_handle(turn, self._dreq(turn), event)
        result = self._adopt_result(turn, result)
        self._adopt(turn, result.events, cards=False)
        if await self._control_flags(turn, result):
            return
        if result.change_ops or result.changed_slots:
            asked = isinstance(event, OptionSelected) or (
                isinstance(event, UserMessage) and result.intent == "change"
            )
            await self._apply_change_result(turn, result, derived=not asked)

    # ================================================================== the plan job
    #
    # Every unit of work below is short and runs between engine calls (research, build): the
    # engines run with no connection held. Writes go through ``_job_tx``, which re-proves under
    # the session row lock that this job is still the session's current run.

    async def _retire_job(self, jc: _JobCtx, error: str) -> None:
        """End the job for good (never retried): cancelled, lost or failed. A job this worker no
        longer owns is left alone."""
        try:
            async with self.store.begin(jc.tenant_id) as uow:
                await self._discard_burned(uow, jc)
                await self.runs.finish(uow, jc.job, jc.worker_id, error=error)
        except (Conflict, NotFound):
            log.warning("job_retire_skipped", job_id=jc.job.id)  # someone else already ended it

    @staticmethod
    async def _discard_burned(uow: UnitOfWork, jc: _JobCtx) -> None:
        """A run that ends without using the version numbers it reserved retires those drafts."""
        for version in jc.burned:
            rec = await uow.specs.get(jc.session_id, version)
            if rec is not None and rec.status == "draft":
                await uow.specs.set_status(jc.session_id, version, "superseded")

    def _run_ctx(self, jc: _JobCtx) -> RunContext:
        async def progress(
            phase: PlanPhase, step: str, label: str, done: int = 0, total: int = 0
        ) -> None:
            await self.runs.checkpoint(self.store, jc.job, jc.token, jc.worker_id)
            async with self.store.begin(jc.tenant_id) as uow:
                await uow.events.append_outbound(
                    jc.session_id,
                    ProgressEvent(
                        event_id=self.ids("evt"),
                        session_id=jc.session_id,
                        at=self.clock.now(),
                        phase=phase,
                        step=step,
                        label=label,
                        done=done,
                        total=total,
                    ),
                )

        return RunContext(
            session_id=jc.session_id,
            tenant_id=jc.tenant_id,
            request_id=self.ids("req"),
            run_id=jc.job.id,
            cancel=jc.token,
            progress=progress,
        )

    async def _is_current(self, uow: UnitOfWork, jc: _JobCtx, rec: SessionRecord | None) -> bool:
        """This job is the session's run: it is PLANNING and the job is running under this worker
        and nobody asked to cancel it (a recovered or finished job is no longer ours)."""
        if rec is None or rec.state != "PLANNING" or jc.token.cancelled:
            return False
        job = await uow.jobs.get(jc.job.id)
        return (
            job is not None
            and job.state == "running"
            and job.lock_owner == jc.worker_id
            and not job.cancel_requested
        )

    async def _job_turn(
        self, uow: UnitOfWork, jc: _JobCtx, ctx: RunContext, journal: list[_Recorded]
    ) -> _Turn:
        """Lock the session and prove this job is still its current run (else cancel quietly)."""
        rec = await uow.sessions.get(jc.session_id, for_update=True)
        if not await self._is_current(uow, jc, rec):
            raise CallCancelled("this run is no longer current")
        assert rec is not None
        return self._turn(
            uow, rec, self._policy(rec.policy_id), reply_to=None, ctx=ctx, journal=journal
        )

    async def _job_tx(self, jc: _JobCtx, body: Callable[[_Turn], Awaitable[None]]) -> _Turn:
        """One short, replayable unit of work for the job (see ``_run_turn``); returns the pass
        that committed."""
        ctx = self._ctx_for(jc.session_id, jc.tenant_id, self.ids("req"))

        async def attempt(uow: UnitOfWork, journal: list[_Recorded]) -> _Outcome:
            turn = await self._job_turn(uow, jc, ctx, journal)
            await body(turn)
            return _Outcome(await self._flush(turn), turn)

        outcome = await self._run_turn(jc.tenant_id, attempt)
        assert outcome.turn is not None
        return outcome.turn

    @staticmethod
    async def _beat(uow: UnitOfWork, jc: _JobCtx) -> None:
        """Heartbeat; a job this worker lost (recovered, finished) ends quietly."""
        try:
            await uow.jobs.heartbeat(jc.job.id, jc.worker_id)
        except (Conflict, NotFound):
            raise CallCancelled("this worker no longer owns the job") from None

    async def _run_job(self, jc: _JobCtx) -> None:
        job, payload = jc.job, jc.job.payload
        intent = decode_intent(payload.change_ops_json)
        async with self.store.begin(jc.tenant_id) as uow:
            turn = await self._job_turn(
                uow, jc, self._ctx_for(jc.session_id, jc.tenant_id, self.ids("req")), []
            )
            await self._beat(uow, jc)
            rec, policy = turn.rec, turn.policy
            brief_rec = await uow.briefs.get(jc.session_id, payload.brief_version)
            note_rec = await uow.research.latest(jc.session_id)
            prev_spec = await self._current_spec(uow, rec)
            all_specs = await uow.specs.list_versions(jc.session_id)
        if brief_rec is None:
            await self._fail_job(jc, "the approved Brief is missing")
            return
        # The version this run builds. Normally the number it was enqueued with; but a number that
        # already has a row belongs to an attempt that died after reserving it (see ``_job_done``),
        # and a number that reached the project repo is never reused.
        spec_version = max(payload.spec_version, max((s.version for s in all_specs), default=0) + 1)
        note: ResearchNote | None = note_rec.note if note_rec else None
        ran_research = False
        if rec.plan_phase == "research":
            if policy.research.enabled and await self._budget_halts(jc):
                return
            note, rec = await self._research_phase(jc, rec, policy, brief_rec, note)
            ran_research = note.status != "unavailable"
        await self.runs.checkpoint(self.store, job, jc.token, jc.worker_id)
        if await self._budget_halts(jc):
            return
        req = self._build_request(
            rec, policy, payload, intent, brief_rec, note, prev_spec, all_specs, spec_version
        )
        try:
            result = await self.builder.build(req, self._run_ctx(jc))  # no connection held
        except CallCancelled:
            raise
        except BudgetExceeded:
            await self._fail_job(
                jc, "budget reached", notice=(messages.N_BUDGET_REACHED, messages.BUDGET_REACHED)
            )
            return
        except Exception:
            log.exception("build_failed", session_id=jc.session_id, job_id=job.id)
            await self._fail_job(jc, "the spec builder failed")
            return
        if result.status == "needs_answer":
            await self._job_needs_answer(jc, result)
            return
        problem = _check_build(result)
        if problem is not None:
            log.error("build_result_invalid", problem=problem, session_id=jc.session_id)
            await self._fail_job(jc, problem)
            return
        try:
            await self._job_done(jc, result, prev_spec, intent, ran_research, spec_version)
        except HandoffFailed:
            # a light post-handoff change could not be committed: nothing was saved (the unit of
            # work rolled back), so the user goes back to where they were and can ask again
            await self._fail_job(jc, "the approved spec could not be saved")

    async def _budget_halts(self, jc: _JobCtx) -> bool:
        """Before a plan phase: is the budget gone? Then stop the run cleanly (True)."""
        verdict = await self._budget_now(jc.session_id, jc.tenant_id)
        if verdict.ok:
            return False
        log.warning("plan_job_budget_halt", reason=verdict.reason, session_id=jc.session_id)

        async def body(turn: _Turn) -> None:
            self._transition(turn, "stop")  # back to where the run started ...
            kind: HoldKind = "gate1" if turn.rec.state == "BRIEF_REVIEW" else "dropped"
            self._hold_for_budget(turn, verdict, BudgetHold(kind=kind))  # ... and paused there
            await self._discard_burned(turn.uow, jc)
            await self.runs.finish(turn.uow, jc.job, jc.worker_id, error="budget paused")

        try:
            await self._job_tx(jc, body)
        except CallCancelled:
            await self._retire_job(jc, "cancelled")
        return True

    def _build_request(
        self,
        rec: SessionRecord,
        policy: Policy,
        payload: PlanJobPayload,
        intent: JobIntent,
        brief_rec: BriefVersionRecord,
        note: ResearchNote | None,
        prev_spec: SpecVersionRecord | None,
        all_specs: Sequence[SpecVersionRecord],
        spec_version: int,
    ) -> BuildRequest:
        sections = [
            _SECTION_BY_NAME[s] for s in payload.affected_sections if s in _SECTION_BY_NAME
        ] or ["all"]
        first = next((s for s in all_specs if s.brief_version == payload.brief_version), None)
        answers = {
            op.slot_id: rec.discovery.slots[op.slot_id]
            for op in intent.answers
            if op.slot_id and op.slot_id in rec.discovery.slots
        }
        fields: dict[str, object] = {
            "session_id": rec.id,
            "tenant_id": rec.tenant_id,
            "app_id": rec.app_id,
            "spec_version": spec_version,
            "brief": brief_rec.brief,
            "slots": dict(rec.discovery.slots),
            "policy": policy,
            "research": note,
            "excluded_research_ids": list(payload.excluded_research_ids),
            "previous_spec": prev_spec.spec if prev_spec else None,
            "affected_sections": sections,
            "gate1_spec_ids": sorted(_spec_ids(first)) if first else [],
            "gate1_spec": first.spec if first else None,
            "change_ops": list(intent.ops),  # the pending change requests this run honours
            "answer_slots": answers,
            "screen_notes": bookkeeping.screen_notes(rec.discovery),  # BUG-3: details per screen
        }
        return BuildRequest.model_validate(fields)

    async def _research_phase(
        self,
        jc: _JobCtx,
        rec: SessionRecord,
        policy: Policy,
        brief_rec: BriefVersionRecord,
        prev_note: ResearchNote | None,
    ) -> tuple[ResearchNote, SessionRecord]:
        job, payload = jc.job, jc.job.payload
        depth = policy.research.depth
        warn = False
        flagged = False  # the engine ignored instructions it found in a page
        if not policy.research.enabled:
            note = ResearchNote.unavailable(brief_rec.brief.hash, "policy", depth)
        else:
            await self.runs.checkpoint(self.store, job, jc.token, jc.worker_id)
            req = ResearchRequest(
                session_id=rec.id,
                tenant_id=rec.tenant_id,
                run_id=job.id,
                brief=brief_rec.brief,
                slots=dict(rec.discovery.slots),
                policy=policy,
                depth=depth,
                previous_note=prev_note,
                references=references(rec.discovery.references),
                brand_url=brand_url(rec.discovery.slots),
                rerun_jobs=[_JOB_BY_NAME[j] for j in payload.rerun_jobs if j in _JOB_BY_NAME]
                if payload.rerun_jobs is not None
                else None,
            )
            try:
                note, flagged = await self._run_research(req, self._run_ctx(jc))  # no connection
            except CallCancelled:
                raise
            except BudgetExceeded:
                note = ResearchNote.unavailable(brief_rec.brief.hash, "budget", depth)
            except Exception:
                log.exception("research_failed", session_id=rec.id, job_id=job.id)
                note = ResearchNote.unavailable(brief_rec.brief.hash, "failed", depth)
        warn = note.status == "unavailable"
        saved: list[ResearchNote] = []

        async def persist(turn: _Turn) -> None:
            uow = turn.uow
            latest = await uow.research.latest(rec.id)
            version = (latest.note.version if latest else 0) + 1
            stamped = note.model_copy(
                update={"version": version, "created_at": self.clock.now()}
            ).with_hash()
            await uow.research.add(
                ResearchNoteRecord(session_id=rec.id, tenant_id=rec.tenant_id, note=stamped)
            )
            turn.rec = machine.advance_phase_record(turn.rec).model_copy(
                update={"research_version": version}
            )
            await uow.jobs.set_phase(job.id, "build")
            if warn:
                self._notice(
                    turn, messages.N_RESEARCH_UNAVAILABLE, messages.RESEARCH_UNAVAILABLE, "warn"
                )
            if flagged:
                self._notice(turn, messages.N_CONTENT_FLAGGED, messages.CONTENT_FLAGGED)
            saved[:] = [stamped]

        turn = await self._job_tx(jc, persist)
        return saved[0], turn.rec

    async def _run_research(
        self, req: ResearchRequest, ctx: RunContext
    ) -> tuple[ResearchNote, bool]:
        """Run research; also say whether it ignored instructions found in what it read (an engine
        that reports that, see ``_ReportingResearch``; the plain port cannot)."""
        engine = self.research
        if isinstance(engine, _ReportingResearch):
            report = await engine.run_with_report(req, ctx)
            return report.note, len(report.injection_events) > 0
        return await engine.run(req, ctx), False

    async def _job_needs_answer(self, jc: _JobCtx, result: BuildResult) -> None:
        if result.question is None:
            await self._fail_job(jc, "the builder asked a question without a card")
            return
        question = result.question

        async def body(turn: _Turn) -> None:
            self._transition(turn, "needs_answer")
            fresh = bookkeeping.without_flow_keys(
                turn.rec.discovery, bookkeeping.ANSWER_TRIES, bookkeeping.ANSWER_OFFER
            )
            turn.rec = turn.rec.model_copy(
                update={"discovery": fresh.model_copy(update={"pending_card": question})}
            )
            self._emit(
                turn, CardEvent(event_id=_PLACEHOLDER, session_id=_PLACEHOLDER, card=question)
            )
            await turn.uow.jobs.complete(jc.job.id)

        await self._job_tx(jc, body)

    async def _job_done(
        self,
        jc: _JobCtx,
        result: BuildResult,
        prev: SpecVersionRecord | None,
        intent: JobIntent,
        ran_research: bool,
        spec_version: int,
    ) -> None:
        """Persist the new draft, then open Gate 2 (or hand off a light post-handoff change).

        **The version number is reserved before the project repo can see it.** A run that started
        after hand-off may commit its spec to the repo inside the transaction below (the light
        path); if that transaction then rolls back or the process dies, the repo keeps
        ``spec-v<N>`` while the database never learned about N, and the next run would build a
        *different* spec as the same N and be refused for ever. So such a run first commits the
        draft row alone (one short transaction: number reserved, nothing else changes), and the
        final transaction only moves that draft along. Whatever happens next, N has a row, and the
        next attempt (``_run_job`` takes ``max(...) + 1``) builds N + 1.
        """
        payload = jc.job.payload
        assert result.spec is not None
        assert result.spec_text is not None
        assert result.spec_hash is not None
        assert result.plan is not None
        built_spec, built_text, built_hash, built_plan = (
            result.spec,
            result.spec_text,
            result.spec_hash,
            result.plan,
        )

        async def draft_record(uow: UnitOfWork, rec: SessionRecord) -> SpecVersionRecord:
            note_rec = await uow.research.latest(rec.id)
            plan = built_plan.model_copy(
                update={"spec_version": spec_version, "spec_hash": built_hash}
            )
            return SpecVersionRecord(
                session_id=rec.id,
                tenant_id=rec.tenant_id,
                version=spec_version,
                hash=built_hash,
                status="draft",
                spec_text=built_text,
                spec=built_spec,
                plan=plan,
                brief_version=payload.brief_version,
                research_version=note_rec.note.version if note_rec else None,
                created_at=self.clock.now(),
            )

        reserve = payload.origin == "HANDED_OFF"
        if reserve:

            async def reserve_version(turn: _Turn) -> None:
                if await turn.uow.specs.get(turn.rec.id, spec_version) is None:
                    await turn.uow.specs.add(await draft_record(turn.uow, turn.rec))

            await self._job_tx(jc, reserve_version)
            jc.burned.append(spec_version)

        async def body(turn: _Turn) -> None:
            uow, rec, policy = turn.uow, turn.rec, turn.policy
            note_rec = await uow.research.latest(rec.id)
            spec_rec = await uow.specs.get(rec.id, spec_version) if reserve else None
            if spec_rec is None:
                spec_rec = await uow.specs.add(await draft_record(uow, rec))
            for old in await uow.specs.list_versions(rec.id):
                if old.version != spec_rec.version and old.status == "draft":
                    await uow.specs.set_status(rec.id, old.version, "superseded")
            turn.rec = rec.model_copy(update={"spec_version": spec_rec.version})
            await uow.jobs.complete(jc.job.id)
            self._emit(
                turn,
                PlanReady(
                    event_id=_PLACEHOLDER,
                    session_id=_PLACEHOLDER,
                    version=spec_rec.version,
                    hash=spec_rec.hash,
                ),
            )
            if rec.origin == "HANDED_OFF":
                await self._finish_post_handoff(
                    turn, spec_rec, prev, intent, result, ran_research, policy, note_rec
                )
            else:
                self._transition(turn, "plan_ready")
                lines = plan_diff(prev.spec, spec_rec.spec).lines if prev else []
                await self._present_gate(turn, "plan", diff_lines=lines)
            await self._drain_queue(turn)

        await self._job_tx(jc, body)

    async def _finish_post_handoff(
        self,
        turn: _Turn,
        spec_rec: SpecVersionRecord,
        prev: SpecVersionRecord | None,
        intent: JobIntent,
        result: BuildResult,
        ran_research: bool,
        policy: Policy,
        note_rec: ResearchNoteRecord | None,
    ) -> None:
        """Spec 3.6: light applies directly (the request is the approval); heavy re-opens Gate 2."""
        new_screens = diff_ids(prev.spec, spec_rec.spec).new_screens if prev else 0
        change: ChangePlan = plan_change(intent.ops, note=note_rec.note if note_rec else None)
        verdict = classify_post_handoff(
            change,
            policy=policy.gates.post_handoff,
            plan_roles=gates.required_roles(policy, "plan"),
            new_screens=new_screens,
            repaired=result.repair_rounds > 0,
            research_ran=ran_research,
            derived=intent.derived,
        )
        if verdict.kind == "heavy":
            self._transition(turn, "plan_ready")
            lines = plan_diff(prev.spec, spec_rec.spec).lines if prev else []
            self._say(turn, messages.HEAVY_NEEDS_REVIEW)
            await self._present_gate(turn, "plan", diff_lines=lines)
            return
        rec = turn.rec
        approval = ApprovalRecord(
            id=self.ids("apr"),
            session_id=rec.id,
            gate="plan",
            version=spec_rec.version,
            hash=spec_rec.hash,
            approver_id=rec.owner_id,
            role=gates.REQUESTER,
            decision="approve",
            via=intent.via,
            comment=gates.LIGHT_CHANGE_COMMENT,
            at=self.clock.now(),
        )
        await turn.uow.approvals.add(approval)
        gates.assert_handoff_allowed(
            version=spec_rec.version,
            spec_hash=spec_rec.hash,
            spec_text=spec_rec.spec_text,
            roles=gates.required_roles(policy, "plan"),
            approvals=[approval],
        )
        await self._handoff(turn, spec_rec, transition="plan_ready", light=True)
        self._say(turn, messages.CHANGE_APPLIED.format(version=spec_rec.version))

    async def _fail_job(
        self, jc: _JobCtx, why: str, *, notice: tuple[str, str] | None = None
    ) -> None:
        """The run failed: return to where it started with a friendly notice, never a raw error."""
        log.warning("plan_job_failed", why=why, session_id=jc.session_id, job_id=jc.job.id)

        async def body(turn: _Turn) -> None:
            self._transition(turn, "stop")
            code, text = notice or (messages.N_BUILD_FAILED, messages.BUILD_FAILED)
            self._notice(turn, code, text, "warn")
            await self._represent(turn)
            await self._discard_burned(turn.uow, jc)
            await self.runs.finish(turn.uow, jc.job, jc.worker_id, error="failed")

        try:
            await self._job_tx(jc, body)
        except CallCancelled:
            await self._retire_job(jc, "cancelled")

    async def _abort_job(self, jc: _JobCtx) -> None:
        """Unexpected exception inside a job: leave the session usable, then the caller re-raises."""
        try:
            await self._fail_job(jc, "unexpected error")
        except Exception:  # pragma: no cover - last resort, never mask the original error
            log.exception("abort_job_failed", job_id=jc.job.id)


# ====================================================================== helpers


def _slot_text(rec: SlotRecord) -> str:
    if rec.value is None:
        return ""
    if isinstance(rec.value, list):
        return ", ".join(rec.value)
    return str(rec.value)


def _ops_from_slots(
    slot_ids: Sequence[str], slots: dict[str, SlotRecord], instruction: str | None = None
) -> list[ChangeOp]:
    """Synthesise set/clear ops from slots an engine says it wrote (when it sent no ops)."""
    ops: list[ChangeOp] = []
    for sid in slot_ids:
        if sid not in SLOT_IDS:
            continue  # an engine that names a slot we do not know cannot drive a change run
        rec = slots.get(sid)
        if rec is None or rec.state == "empty":
            ops.append(ChangeOp.model_validate({"kind": "clear_slot", "slot_id": sid}))
            continue
        fields: dict[str, object] = {"kind": "set_slot", "slot_id": sid, "instruction": instruction}
        if isinstance(rec.value, list):
            fields["value_list"] = rec.value
        elif isinstance(rec.value, bool):
            fields["value_bool"] = rec.value
        elif rec.value is not None:
            fields["value_text"] = str(rec.value)
        ops.append(ChangeOp.model_validate(fields))
    return ops


def _op_value(op: ChangeOp) -> SlotData:
    """The value a ``set_slot`` change op names (None when it names none)."""
    if op.value_list is not None:
        return op.value_list
    if op.value_bool is not None:
        return op.value_bool
    return op.value_text.strip() if op.value_text is not None else None


def _slot_name(slot_id: str) -> str:
    """A slot id as words for a notice ("design.direction" -> "Design direction")."""
    text = slot_id.replace(".", " ").replace("_", " ")
    return text[:1].upper() + text[1:]


def _show(value: SlotData) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    return "nothing" if value is None else str(value)


def _roles_text(roles: Sequence[str]) -> str:
    return " and ".join(sorted(roles)) or "someone"


def _same_words(a: str, b: str) -> bool:
    """Equal ignoring case, punctuation and spacing (a typed "Use the default!" is the chip)."""

    def norm(text: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", text).casefold().split())

    return norm(a) == norm(b)


def _ops_from_action(event: UiAction) -> list[ChangeOp] | None:
    """Toggle actions as change ops (None for an action that is not a change)."""
    target = event.target
    if not target:
        return None
    if event.action == "toggle_assumption":
        if target not in SLOT_IDS:
            return None  # not a slot: nothing we can toggle
        return [
            ChangeOp.model_validate(
                {
                    "kind": "other",
                    "slot_id": target,
                    "value_bool": event.value,
                    "instruction": "toggle_assumption",
                }
            )
        ]
    if event.action == "toggle_feature":
        return [ChangeOp(kind="toggle_feature", target_id=target, value_bool=event.value)]
    if event.action == "toggle_research_item":
        return [ChangeOp(kind="toggle_research_item", target_id=target, value_bool=event.value)]
    return None


def _spec_ids(spec: SpecVersionRecord | None) -> set[str]:
    return spec_ids(spec.spec) if spec is not None else set()


def _check_build(result: BuildResult) -> str | None:
    """A build result is only usable when it is complete and its hash is the hash of its text."""
    if result.spec is None or result.spec_text is None or result.plan is None:
        return "the builder returned an incomplete result"
    if result.spec_hash is None or compute_spec_hash(result.spec_text) != result.spec_hash:
        return "the builder's hash does not match its spec text"
    return None


__all__ = ["DEFAULT_POLICY_IDS", "RESEARCH_STALE_DAYS", "FlowService"]
