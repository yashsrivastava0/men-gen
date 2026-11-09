"""Engine ports: one Protocol per structured call, over DOMAIN types (not wire models), plus the service and
hand-off ports. This is what decouples the engine from the call folders (prompt section 7, ADR 0014):

* each ``calls/<name>/`` folder ships an adapter implementing its port (spec + schema + prompt + mock +
  deterministic fallback, run through ``CallRunner``);
* the engine consumes the ports and uses the deterministic fallbacks as its test doubles.

Control flow stays in code: the model never chooses what to do next, it fills schemas (prompt 7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card, CardOption, CardType
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.claims import Claim, Statement
from mde_compass.domain.common import Basis, Status, Strict
from mde_compass.domain.events import InboundEvent, StoredEvent
from mde_compass.domain.research import ResearchRequest, SearchHints
from mde_compass.domain.session import Mode, SessionRecord
from mde_compass.domain.situation import (
    DepthProfile,
    DomainHypothesis,
    SituationProfile,
)

# --- the compact context every call receives (never the whole chat) -------------------------------


class SlotView(Strict):
    slot_id: str
    value: JsonValue = None
    state: Status = "empty"
    confidence: float = 0.0


class TurnLine(Strict):
    role: Literal["user", "assistant"]
    text: str


class TurnContext(Strict):
    """The situation profile and the Brief so far, as slot views, plus at most a few recent turns."""

    session_id: str
    tenant_id: str
    language: str = "en"
    mode: Mode = "guided"
    depth: DepthProfile = "standard"
    profile: SituationProfile | None = None
    slots: list[SlotView] = Field(default_factory=list)
    recent_turns: list[TurnLine] = Field(
        default_factory=list
    )  # at most config.gate.history_slice_turns
    pending_card: Card | None = None
    question_n: int = 0


# --- understand_turn --------------------------------------------------------------------------------

Intent = Literal[
    "answer",
    "greeting",
    "you_decide",
    "finish_with_defaults",
    "change_request",
    "question",
    "off_topic",
    "confirm",
    "negate",
    "price_or_time",
    "tech_choice",
    "unsafe",
    "stop",
    "other",
]


class SlotUpdate(Strict):
    slot_id: str
    value: JsonValue
    quote: str = ""  # the user's words that justify it (evidence); empty means "assumed"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: Literal["user_text", "voice", "assumed", "model_knowledge"] = "user_text"
    remove: bool = False  # explicit negation clears the slot


class TurnUnderstanding(Strict):
    intent: Intent = "other"
    slot_updates: list[SlotUpdate] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)  # apps the user likes ("like X")
    language: str = "en"
    safety_flags: list[str] = Field(
        default_factory=list
    )  # secret, injection, unsafe, declined_category
    ambiguous: list[str] = Field(default_factory=list)  # slot ids needing an either/or
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Understander(Protocol):
    async def understand(
        self, ctx: TurnContext, text: str, *, input_mode: Literal["text", "voice"] = "text"
    ) -> TurnUnderstanding: ...


# --- write_card -------------------------------------------------------------------------------------


class CardRequest(Strict):
    frame_id: str
    slots: list[str]
    card_type: CardType
    option_source: Literal["document", "family", "model", "fixed"] = "model"
    hints: list[str] = Field(
        default_factory=list
    )  # typical features/roles from families.yaml, document quotes
    evidence: list[str] = Field(
        default_factory=list
    )  # user's words and document quotes to ground options
    must_confirm: bool = False
    avoid_labels: list[str] = Field(default_factory=list)  # options already shown (no repeats)


class CardDraft(Strict):
    """What the card writer returns; the engine validates it, then makes it a ``Card`` (id, progress)."""

    question: str
    why_we_ask: str = ""
    options: list[CardOption] = Field(default_factory=list)
    placeholder: str = ""
    language: str = "en"


class CardWriter(Protocol):
    async def write(self, ctx: TurnContext, request: CardRequest) -> CardDraft: ...


# --- situate ----------------------------------------------------------------------------------------


class SituationResult(Strict):
    profile: SituationProfile
    hypothesis: DomainHypothesis
    one_line_idea: str = ""


class Situator(Protocol):
    async def situate(
        self, ctx: TurnContext, *, user_text: str, statements: list[Statement] | None = None
    ) -> SituationResult: ...


# --- compile_text -----------------------------------------------------------------------------------


class ItemDescription(Strict):
    item_id: str  # a path in the ChangeSet grammar, e.g. "scope.FTR-004"
    text: str


class CompiledText(Strict):
    summary: str  # one line
    pitch: str
    descriptions: list[ItemDescription] = Field(
        default_factory=list
    )  # plain-language row descriptions


class Compiler(Protocol):
    async def compile(self, ctx: TurnContext, brief: Brief) -> CompiledText: ...


# --- seed_research ----------------------------------------------------------------------------------


class SeedResult(Strict):
    requests: list[ResearchRequest]
    hints: SearchHints = Field(default_factory=SearchHints)


class ResearchSeeder(Protocol):
    async def seed(self, ctx: TurnContext, brief: Brief) -> SeedResult: ...


# --- probe_handoff ----------------------------------------------------------------------------------

ProbeKind = Literal["research", "blueprint", "design"]


class ProbeGap(Strict):
    question: str  # what the canary could not decide
    slot_id: str | None = None
    fix: Literal["assume", "ask"] = "ask"
    suggested_value: JsonValue = None  # for fix == "assume": the value to propose for approval


class ProbeResult(Strict):
    kind: ProbeKind
    passed: bool
    gaps: list[ProbeGap] = Field(default_factory=list)
    plan: list[str] = Field(
        default_factory=list
    )  # queries / journeys+screens / tone+direction it produced


class Prober(Protocol):
    async def probe(self, ctx: TurnContext, kind: ProbeKind, brief: Brief) -> ProbeResult: ...


# --- answer -----------------------------------------------------------------------------------------


class AnswerResult(Strict):
    text: str
    language: str = "en"
    grounded_in: list[str] = Field(
        default_factory=list
    )  # slot ids / catalog keys the answer relied on
    declined: bool = False  # price, time or capability it must not invent


class Answerer(Protocol):
    async def answer(self, ctx: TurnContext, question: str) -> AnswerResult: ...


# --- interpret_change / make_variants ---------------------------------------------------------------


class ResolvedTarget(Strict):
    kind: Literal["section", "row", "slot", "feature", "assumption", "unknown"] = "unknown"
    id: str = ""  # section key, item path or id


class HistoryLine(Strict):
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    path: str | None = None  # the item this line produced (value history)
    value: JsonValue = None


class ChangeInput(Strict):
    text: str
    target: ResolvedTarget
    history_slice: list[HistoryLine] = Field(default_factory=list)
    reason: str = ""  # feedback reason chip, if any


class ChangeProposal(Strict):
    understood: bool
    ops: list[PatchOp] = Field(default_factory=list)
    clarification: str = ""  # a small either/or question when the target or intent is ambiguous
    options: list[str] = Field(default_factory=list)
    touches_locked: list[str] = Field(
        default_factory=list
    )  # paths of Locked items the ops touch (flagged)
    confidence: float = 0.0


class ChangeInterpreter(Protocol):
    async def interpret(
        self, ctx: TurnContext, change: ChangeInput, brief: Brief
    ) -> ChangeProposal: ...


class Variant(Strict):
    label: str
    value: JsonValue
    reason: str = ""
    basis: Basis = "suggestion"


class VariantSet(Strict):
    variants: list[Variant]  # exactly three, no near-duplicates


class VariantMaker(Protocol):
    async def variants(
        self, ctx: TurnContext, *, path: str, current: JsonValue, reason: str
    ) -> VariantSet: ...


# --- intake-side calls (deferred phase; ports defined so the engine can already depend on them) ------


class MergeResult(Strict):
    claims: list[Claim]
    conflicts: list[tuple[str, str]] = Field(default_factory=list)  # claim id pairs
    tbd: list[str] = Field(default_factory=list)  # open issues that become questions
    coverage: float = 1.0
    statement_count: int = 0
    needs_confirming: int = 0


class AmbiguityFinding(Strict):
    slot_id: str | None
    text: str
    kind: Literal["vague", "conflicting", "undefined"]


class ChunkReader(Protocol):
    async def read(
        self, ctx: TurnContext, *, chunk_text: str, source_id: str, pointer: str
    ) -> list[Statement]: ...


class ClaimMerger(Protocol):
    async def merge(
        self, ctx: TurnContext, statements: list[Statement], *, source_id: str
    ) -> MergeResult: ...


class AmbiguityScanner(Protocol):
    async def scan(self, ctx: TurnContext, brief: Brief) -> list[AmbiguityFinding]: ...


@dataclass(frozen=True)
class Ports:
    """All twelve call ports, injected into the engine. Tests use the deterministic fallbacks."""

    understander: Understander
    card_writer: CardWriter
    situator: Situator
    compiler: Compiler
    seeder: ResearchSeeder
    prober: Prober
    answerer: Answerer
    change_interpreter: ChangeInterpreter
    variant_maker: VariantMaker
    chunk_reader: ChunkReader
    claim_merger: ClaimMerger
    ambiguity_scanner: AmbiguityScanner


# --- hand-off ---------------------------------------------------------------------------------------


class HandoffPort(Protocol):
    """Hands the approved Brief and its consumer views to the next agent. Silence never approves, so this
    is only ever called after Gate 1 approval of the current hash."""

    async def hand_off(
        self,
        *,
        session_id: str,
        version: int,
        brief_hash: str,
        views: dict[str, dict[str, JsonValue]],
    ) -> None: ...


# --- the service the API talks to -------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    """Who is calling (dev auth headers). Tenant isolation is by ``tenant_id``."""

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()


class TurnResult(Strict):
    accepted: bool = True
    duplicate: bool = False
    events: list[StoredEvent] = Field(default_factory=list)


class CompassService(Protocol):
    """Everything the HTTP layer needs; implemented by ``mde_compass.engine.service.Service`` and by a
    scripted fake for API/UI tests. All methods are async and scoped to ``caller.tenant_id``."""

    async def create_session(
        self, caller: Caller, *, mode: Mode, locale: str, user_label: str
    ) -> SessionRecord: ...
    async def list_sessions(self, caller: Caller) -> list[SessionRecord]: ...
    async def get_session(self, caller: Caller, session_id: str) -> SessionRecord: ...
    async def handle_event(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> TurnResult: ...
    async def list_outbound(
        self, caller: Caller, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]: ...
    async def get_brief(
        self, caller: Caller, session_id: str, version: int | None = None
    ) -> dict[str, JsonValue]: ...
    async def brief_view(
        self, caller: Caller, session_id: str, *, version: int | None = None
    ) -> dict[str, JsonValue]: ...
    async def brief_versions(
        self, caller: Caller, session_id: str
    ) -> list[dict[str, JsonValue]]: ...
    async def brief_diff(
        self, caller: Caller, session_id: str, *, from_version: int, to_version: int
    ) -> dict[str, JsonValue]: ...
    async def history(
        self,
        caller: Caller,
        session_id: str,
        *,
        after_idx: int = 0,
        limit: int = 50,
        slot: str | None = None,
        q: str | None = None,
    ) -> dict[str, JsonValue]: ...
    async def provenance(
        self, caller: Caller, session_id: str, item_id: str
    ) -> dict[str, JsonValue]: ...
    async def gate(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> dict[str, JsonValue]: ...
    async def handoff(self, caller: Caller, session_id: str) -> dict[str, JsonValue]: ...
    async def ingest_upload(
        self, caller: Caller, session_id: str, filename: str, data: bytes
    ) -> TurnResult: ...
    async def budget(self) -> dict[str, JsonValue]: ...
    async def calls(
        self, caller: Caller, session_id: str, *, limit: int = 30
    ) -> list[dict[str, JsonValue]]: ...

    async def reopen_slot(
        self, caller: Caller, session_id: str, slot_id: str, reason: str
    ) -> TurnResult:
        """The typed back-edge (docs 11 section 4): a later stage reopens ONE slot with a single targeted
        question, at most ``config.gate.max_back_edge_loops`` times per session, never a new interview.
        Optional: the default says the service has no such hook (the scripted API fake keeps it)."""
        raise NotImplementedError("this service has no back-edge hook")
