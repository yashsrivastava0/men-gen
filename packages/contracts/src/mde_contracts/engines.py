"""Request/result models for the three engines and the shared run context.

The Flow controller is the only caller. Engines are *pure with respect to state*: they receive the
state they need, return the new state and the events to emit, and never write to the database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from mde_contracts.brief import Brief
from mde_contracts.calls import ChangeOp, Intent, TurnLite, ValidatorFinding
from mde_contracts.cards import CardInstance
from mde_contracts.common import Depth, Frozen, GateKind, Mode
from mde_contracts.events import InboundEvent, OutboundEvent
from mde_contracts.gates import PlanPhase, SessionState
from mde_contracts.plan import PlanView
from mde_contracts.policy import Policy
from mde_contracts.research import ResearchJobId, ResearchNote
from mde_contracts.slots import SlotRecord, SlotSource
from mde_contracts.spec import AppSpec
from mde_core.cancel import CancellationToken, NeverCancelled

# --------------------------------------------------------------------------- run context


class ProgressEmitter(Protocol):
    async def __call__(
        self, phase: PlanPhase, step: str, label: str, done: int = 0, total: int = 0
    ) -> None: ...


async def _no_progress(
    phase: PlanPhase, step: str, label: str, done: int = 0, total: int = 0
) -> None:
    return None


@dataclass(frozen=True)
class RunContext:
    """Non-serialisable context handed to engines (cancellation, progress, scopes)."""

    session_id: str
    tenant_id: str
    request_id: str
    run_id: str | None = None
    cancel: CancellationToken = field(default_factory=NeverCancelled)
    progress: Callable[..., Awaitable[None]] = _no_progress

    @property
    def session_scope(self) -> str:
        return f"session:{self.session_id}"

    @property
    def research_scope(self) -> str | None:
        return f"research:{self.run_id}" if self.run_id else None


# --------------------------------------------------------------------------- discovery


class UploadMeta(Frozen):
    upload_id: str
    filename: str
    media_type: str
    summary: str = ""  # what we understood (never the raw text)
    injection_suspected: bool = False


class DiscoveryState(Frozen):
    """Everything the Discover loop needs between turns (persisted as JSONB on the session)."""

    slots: dict[str, SlotRecord] = Field(default_factory=dict)  # slot id -> record
    summary: str = ""  # rolling summary (1 to 2k tokens max)
    recent_turns: list[TurnLite] = []
    pending_card: CardInstance | None = None
    cards_asked: list[str] = []  # card ids (+ ":instance" when repeated)
    card_count: int = 0
    turn_count: int = 0
    language: str = "en"
    skips_in_row: int = 0
    off_topic_streak: int = 0
    nudged_card_ids: list[str] = []
    visual_rounds: dict[str, int] = Field(default_factory=dict)  # card id -> rounds shown
    seen_option_ids: list[str] = []
    sensitive_flags: list[str] = []
    compliance_confirmed: list[str] = []
    references: list[str] = []
    uploads: list[UploadMeta] = []
    help_mode: bool = False
    greeted: bool = False
    finished_with_defaults: bool = False
    extra: dict[str, str] = Field(default_factory=dict)  # additive escape hatch


class DiscoveryRequest(Frozen):
    session_id: str
    tenant_id: str
    mode: Mode
    policy: Policy
    state: DiscoveryState
    session_state: SessionState = "DISCOVERING"
    gate_open: GateKind | None = None
    language_hint: str | None = None
    source: Literal["text", "gate_comment"] = "text"  # gate comments are change requests
    # The tenant's Org Profile (a tolerant JSON document), passed by the flow on the FIRST turn
    # only and only for Enterprise sessions. Discover turns it into `assumed` slots with source
    # `org_profile`; it is untrusted text until screened (spec 10, D-077).
    org_profile: dict[str, JsonValue] | None = None


class DiscoveryResult(Frozen):
    state: DiscoveryState
    events: list[OutboundEvent] = []  # seq == 0; the flow assigns event ids' order and seq
    intent: Intent | None = None
    brief_complete: bool = False  # no required slot left (or finished with defaults / caps)
    approval_confident: bool = False  # typed approval recognised with confidence
    change_ops: list[ChangeOp] = []  # when the text is a change request (C14)
    changed_slots: list[str] = []  # slot ids written this turn
    stop_requested: bool = False
    pause_requested: bool = False
    start_over_requested: bool = False
    llm_calls: int = 0


class DiscoveryEngine(Protocol):
    async def start(self, req: DiscoveryRequest, ctx: RunContext) -> DiscoveryResult:
        """First turn: canned greeting + starter chips, no LLM."""
        ...

    async def handle(
        self, req: DiscoveryRequest, event: InboundEvent, ctx: RunContext
    ) -> DiscoveryResult: ...

    async def reopen_slot(
        self, req: DiscoveryRequest, slot_id: str, ctx: RunContext
    ) -> DiscoveryResult:
        """Gate 1 'change something': show the card of one slot only."""
        ...

    async def apply_changes(
        self, req: DiscoveryRequest, ops: list[ChangeOp], source: SlotSource, ctx: RunContext
    ) -> DiscoveryResult: ...

    async def compile_brief(self, req: DiscoveryRequest, ctx: RunContext) -> Brief:
        """Deterministic rows from slots + the C11 sentence. ``version``/``hash`` set by the flow."""
        ...


# --------------------------------------------------------------------------- research


class ResearchRequest(Frozen):
    session_id: str
    tenant_id: str
    run_id: str
    brief: Brief  # the approved Brief
    slots: dict[str, SlotRecord]
    policy: Policy
    depth: Depth = "light"
    previous_note: ResearchNote | None = None
    rerun_jobs: list[ResearchJobId] | None = None  # change handling: only these jobs
    references: list[str] = []  # named apps ("like X") from Discover; enables R2
    brand_url: str | None = None  # a site the user gave or confirmed; enables R3 (never guessed)


class ResearchEngine(Protocol):
    async def run(self, req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        """Never raises for research failures: returns a note with ``status='unavailable'``."""
        ...


# --------------------------------------------------------------------------- spec builder

SpecSection = Literal[
    "all", "design", "screens", "navigation", "data", "api", "roles", "integrations", "behavior"
]


class BuildRequest(Frozen):
    session_id: str
    tenant_id: str
    app_id: str
    spec_version: int  # version number to assign to the new draft
    brief: Brief  # approved at Gate 1
    slots: dict[str, SlotRecord]  # snapshot bound to the approved Brief
    policy: Policy
    research: ResearchNote | None = None
    excluded_research_ids: list[str] = []  # toggled off at Gate 2
    previous_spec: AppSpec | None = None  # incremental builds / changes
    affected_sections: list[SpecSection] = ["all"]
    gate1_spec_ids: list[str] = []  # ids present in the first draft (diff-since-gate-1)
    change_ops: list[ChangeOp] = []  # pending change requests the build must honour
    screen_notes: dict[
        str, str
    ] = {}  # screen id or route -> a note the user gave in Discover (BUG-3)
    gate1_spec: AppSpec | None = None  # the first draft itself, so changed ids can be computed
    answer_slots: dict[str, SlotRecord] = Field(default_factory=dict)  # needs_answer replies


class BuildResult(Frozen):
    status: Literal["ok", "needs_answer"]
    spec: AppSpec | None = None
    spec_text: str | None = None  # rendered APP_SPEC.md
    # The file hash approvals bind to: sha256 of spec_text with its own front-matter `hash:` value
    # blanked (`mde_spec_loader.hashing.compute_spec_hash`; ADR 0006). It equals the embedded hash.
    spec_hash: str | None = None
    plan: PlanView | None = None
    question: CardInstance | None = None  # plain question derived from a failing validator
    question_slot: str | None = None
    findings: list[ValidatorFinding] = []
    proposals_applied: int = 0
    proposals_dropped: int = 0
    repair_rounds: int = 0


class SpecBuilderEngine(Protocol):
    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult: ...
