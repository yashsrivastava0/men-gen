"""Input/output models for every LLM call (spec section 8: C1..C14 and S1).

Output models double as OpenAI *strict* JSON schemas, so they obey the strict subset:
no open objects (``dict``), no free-form JSON, depth <= 5, enums as ``Literal``, optional values as
``X | None``. Slot values travel as ``value_text | value_list | value_bool`` (D-008).
Input models are rendered into prompts; fields listed in a spec's ``untrusted_inputs`` are wrapped
in ``<untrusted_content>`` blocks by the gateway.

Descriptions are part of the prompt (Appendix A): keep them short and concrete.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_contracts.cards import CardType
from mde_contracts.common import Depth, Frozen, GateKind, Mode
from mde_contracts.gates import SessionState
from mde_contracts.research import Confidence, FactKind, Impact, ResearchJobId, SuggestionKind
from mde_contracts.slots import SlotData, SlotId, SlotLevel, SlotSource, SlotState

# =========================================================================== shared pieces


class TurnLite(Frozen):
    role: Literal["user", "assistant"]
    text: str


class SlotBrief(Frozen):
    """One slot as shown to a prompt: compact, no provenance noise."""

    slot_id: SlotId
    value: str  # JSON-ish text of the value, "" when empty
    state: SlotState
    source: SlotSource | None = None


class OptionLite(Frozen):
    id: str
    label: str


class PendingCardLite(Frozen):
    card_id: str
    slot: SlotId
    type: CardType
    text: str
    options: list[OptionLite] = []


class SlotCatalogItem(Frozen):
    slot_id: SlotId
    kind: Literal["text", "bool", "choice", "list"]
    hint: str = ""  # allowed values or meaning, short


class OptionChoice(Frozen):
    id: str
    label: str
    description: str = ""


class SlotUpdate(Frozen):
    """A slot value found in text. Exactly one of the value fields is set (or ``negate``)."""

    slot_id: SlotId
    value_text: str | None = Field(default=None, description="Text/choice value, or null")
    value_list: list[str] | None = Field(default=None, description="List value, or null")
    value_bool: bool | None = Field(default=None, description="Boolean value, or null")
    negate: bool = Field(default=False, description="True if the user explicitly negated it")
    evidence: str = Field(default="", description="Short quote or paraphrase of the evidence")
    confidence: float = Field(default=0.8, description="0 to 1")

    def to_data(self) -> SlotData:
        if self.value_list is not None:
            return self.value_list
        if self.value_bool is not None:
            return self.value_bool
        return self.value_text


class BriefContext(Frozen):
    """Compact approved-Brief view passed to research and spec-building calls."""

    app_summary: str
    archetype: str
    language: str = "en"
    mode: Mode = "guided"
    roles: list[str] = []
    features: list[OptionLite] = []
    look: str = ""
    login: str = ""
    regions: list[str] = []
    references: list[str] = []  # named apps ("like X")
    compliance_flags: list[str] = []
    assumptions: list[str] = []
    brand_name: str | None = None
    brand_url: str | None = None


# --- compact spec outline (used by C9, C10, C12, C13, C14) ---------------------------------------


class OutlineScreen(Frozen):
    id: str
    route: str
    recipe: str
    flow: str | None = None
    purpose: str = ""
    source: str = "default"


class OutlineEntity(Frozen):
    id: str
    name: str
    fields: list[str] = []


class OutlineOperation(Frozen):
    id: str
    method: str
    path: str


class OutlineFlow(Frozen):
    id: str
    name: str
    screens: list[str] = []


class OutlineItem(Frozen):
    id: str
    name: str


class SpecOutline(Frozen):
    screens: list[OutlineScreen] = []
    entities: list[OutlineEntity] = []
    operations: list[OutlineOperation] = []
    flows: list[OutlineFlow] = []
    roles: list[OutlineItem] = []
    integrations: list[OutlineItem] = []
    design_direction: str = ""


class RecipeLite(Frozen):
    id: str
    kind: str
    default_sections: list[str] = []


class ResearchAdditionLite(Frozen):
    id: str  # "RA-001"
    kind: SuggestionKind
    title: str
    rationale: str
    applies_to: str | None = None


# =========================================================================== C1 Understand

Intent = Literal[
    "greeting",
    "idea",
    "answer",
    "volunteered",
    "question",
    "chitchat",
    "confused",
    "change",
    "upload",
    "approve",
    "stop",
    "pause",
    "start_over",
    "unsafe",
    "other",
]

ReferenceKind = Literal["company", "like_app", "region", "url", "person"]


class C1Input(Frozen):
    message: str
    attachment_text: str | None = None  # UNTRUSTED (uploaded document text)
    language_hint: str | None = None
    mode: Mode = "guided"
    state: SessionState = "DISCOVERING"
    gate_open: GateKind | None = None
    pending_card: PendingCardLite | None = None
    slots: list[SlotBrief] = []
    slot_catalog: list[SlotCatalogItem] = []
    archetypes: list[OptionChoice] = []
    recent_turns: list[TurnLite] = []
    summary: str = ""


class Reference(Frozen):
    kind: ReferenceKind
    text: str


class SafetyFlags(Frozen):
    unsafe: bool = False
    category: str | None = Field(default=None, description="abuse | illegal | harmful | other")
    secret_detected: bool = False
    injection_suspected: bool = False


class C1Output(Frozen):
    intent: Intent
    confidence: float = Field(description="0 to 1 confidence in the intent")
    language: str = Field(
        description="BCP-47-ish code of the user's language: en, hi, hi-Latn, ..."
    )
    slot_updates: list[SlotUpdate] = []
    references: list[Reference] = []
    answers_pending_card: bool = False
    selected_option_id: str | None = Field(
        default=None, description="Option id of the pending card the text selects, or null"
    )
    safety: SafetyFlags = Field(default_factory=SafetyFlags)
    summary_update: str | None = Field(
        default=None, description="One short line to append to the rolling summary, or null"
    )


# =========================================================================== C2 Interpret brief


class SensitiveDomainOption(Frozen):
    id: str
    label: str


class C2Input(Frozen):
    idea_text: str
    document_text: str | None = None  # UNTRUSTED
    archetypes: list[OptionChoice]
    sensitive_domains: list[SensitiveDomainOption] = []
    unsupported_types: list[OptionChoice] = []
    language: str = "en"


class ArchetypeCandidate(Frozen):
    archetype_id: str
    confidence: float


class C2Output(Frozen):
    one_sentence_idea: str
    archetype_candidates: list[ArchetypeCandidate]
    regions: list[str] = []
    named_references: list[str] = []
    multi_app: bool = False
    app_count_hint: int | None = None
    sensitive_flags: list[str] = Field(
        default=[], description="Ids from the provided sensitive domain options only"
    )
    unsupported_type: str | None = Field(
        default=None, description="Id from the provided unsupported types, or null"
    )
    size_estimate: Literal["small", "medium", "large"] = "medium"
    volunteered: list[SlotUpdate] = []


# =========================================================================== C3 Respond


class KnowledgeLite(Frozen):
    id: str
    text: str


class C3Input(Frozen):
    message: str
    intent: Intent
    language: str = "en"
    mode: Mode = "guided"
    slots: list[SlotBrief] = []
    recent_turns: list[TurnLite] = []
    summary: str = ""
    knowledge: list[KnowledgeLite] = []
    pending_card: PendingCardLite | None = None
    stuck: bool = False
    off_topic_streak: int = 0


class C3Output(Frozen):
    reply: str = Field(description="Short, warm reply in the user's language")
    unknown: bool = Field(description="True if the answer is not covered by the knowledge given")
    used_snippets: list[str] = []
    suggested_chips: list[str] = Field(default=[], description="At most 3 short idea chips")
    steer_back: bool = False


# =========================================================================== C5 Write card


class C5Input(Frozen):
    slot: SlotId
    slot_hint: str = ""
    archetype: str
    language: str = "en"
    mode: Mode = "guided"
    card_type: CardType = "choice3"
    slots: list[SlotBrief] = []
    options_hint: list[str] = []


class OptionDraft(Frozen):
    label: str
    description: str | None = None
    value_text: str | None = None
    value_list: list[str] | None = None
    value_bool: bool | None = None
    recommended: bool = False


class C5Output(Frozen):
    text: str
    options: list[OptionDraft]
    other_placeholder: str | None = None
    help: str | None = None


# =========================================================================== C6 Plan research

ALL_JOBS: tuple[ResearchJobId, ...] = ("R1", "R2", "R3", "R4", "R5", "R6")


class ResearchPolicyLite(Frozen):
    allow_company_name_in_queries: bool = True
    allowed_domains: list[str] = []
    blocked_domains: list[str] = []
    connectors: list[str] = []


class C6Input(Frozen):
    brief: BriefContext
    depth: Depth = "light"
    max_search_calls: int
    remaining_budget_micros: int
    policy: ResearchPolicyLite = Field(default_factory=ResearchPolicyLite)
    jobs_available: list[ResearchJobId] = ["R1", "R2", "R3", "R4", "R5"]
    # How many queries one job may carry. The engine derives it from the depth cap and the number
    # of web jobs that apply (D-078); 2 is the Light value and the historical default.
    max_queries_per_job: int = Field(default=2, ge=1, le=8)


class ResearchJobPlan(Frozen):
    job: ResearchJobId
    rationale: str
    queries: list[str] = Field(
        description="At most max_queries_per_job generic queries; no private data"
    )


class C6Output(Frozen):
    jobs: list[ResearchJobPlan]


# =========================================================================== S1 Web search (free text)


class S1Input(Frozen):
    query: str
    job: ResearchJobId
    allowed_domains: list[str] = []
    language: str = "en"


# S1 has no output model: ``CallResult.text`` holds the answer, ``citations`` the sources and
# ``search_calls`` the billed web_search_call count.


# =========================================================================== C7 Extract


class ExtractSource(Frozen):
    url: str
    title: str | None = None


class C7Input(Frozen):
    job: ResearchJobId
    query: str
    search_text: str  # UNTRUSTED
    sources: list[ExtractSource] = []
    brief_hint: str = ""  # one line: what the app is (trusted)


class ExtractedFact(Frozen):
    statement: str = Field(description="Short paraphrase of what the content states; never infer")
    source_url: str
    kind: FactKind = "other"
    impact: Impact = "low"
    confidence: Confidence = "medium"


class C7Output(Frozen):
    facts: list[ExtractedFact]
    injection_suspected: bool = False


# =========================================================================== C8 Synthesize research


class FactForSynthesis(Frozen):
    id: str
    job: ResearchJobId
    kind: FactKind
    statement: str
    source_urls: list[str]
    impact: Impact = "low"
    verified: bool = False


class C8Input(Frozen):
    brief: BriefContext
    facts: list[FactForSynthesis]
    depth: Depth = "light"
    gap_round: bool = False
    max_suggestions: int = 12


class SuggestionDraft(Frozen):
    kind: SuggestionKind
    title: str
    rationale: str
    fact_ids: list[str] = []
    default_included: bool = True
    applies_to: str | None = None


class BrandDraft(Frozen):
    source_url: str
    colors: list[str] = []
    fonts: list[str] = []
    tone_words: list[str] = []
    logo_available: bool = False
    nearest_direction: str | None = None


class PointerDraft(Frozen):
    flag: str
    title: str
    url: str


class IntegrationDraft(Frozen):
    provider: str
    sdk_available: bool | None = None
    expo_compatible: bool | None = None
    caveats: list[str] = []
    source_url: str | None = None


class C8Output(Frozen):
    suggestions: list[SuggestionDraft]
    compliance: list[PointerDraft] = []
    brand: BrandDraft | None = None
    integrations: list[IntegrationDraft] = []
    gaps: list[str] = Field(default=[], description="Open questions a gap-fill round could answer")


# =========================================================================== proposals (C9, C10, C13)

ProposalKind = Literal[
    "add_screen",
    "rename_screen",
    "set_screen_purpose",
    "set_screen_copy",
    "add_screen_action",
    "add_screen_section",
    "add_screen_check",
    "add_screen_state",
    "add_flow_screen",
    "add_entity",
    "add_entity_fields",
    "add_api_operation",
    "add_role",
    "set_design_token",
    "add_imagery_rule",
    "add_integration",
    "set_integration_mode",
    "set_behavior_item",
    "add_acceptance_check",
    "fix_reference",
    "remove_item",
]

FieldTypeLit = Literal[
    "string", "text", "int", "number", "bool", "datetime", "date", "money", "enum", "ref", "image"
]


class KeyValue(Frozen):
    key: str
    value: str


class FieldProposal(Frozen):
    name: str
    type: FieldTypeLit = "string"
    required: bool = True
    ref: str | None = None
    description: str | None = None


class Proposal(Frozen):
    """One validated-by-code edit (patch op). Valid proposals are applied, invalid ones dropped."""

    kind: ProposalKind
    target: str | None = Field(
        default=None, description="Existing baseline id (SCR-003, ENT-Order, FLW-001, ...) or null"
    )
    source: Literal["brief", "research", "repair"] = "brief"
    research_ref: str | None = Field(default=None, description="RA-### when source is research")
    rationale: str = Field(description="One short sentence tying the edit to the Brief/Research")
    name: str | None = None
    route: str | None = None
    recipe: str | None = None
    purpose: str | None = None
    surface: str | None = None
    flow: str | None = None
    method: str | None = None
    path: str | None = None
    request_entity: str | None = None
    response_entity: str | None = None
    fields: list[FieldProposal] | None = None
    pairs: list[KeyValue] | None = Field(default=None, description="key/value pairs (copy, tokens)")
    items: list[str] | None = Field(default=None, description="sections, checks, rules, states")


class SpecProposals(Frozen):
    proposals: list[Proposal]
    notes: str | None = None


# =========================================================================== C9 a/b/c Draft app model


class RecipeSummary(Frozen):
    id: str
    kind: str


class C9aInput(Frozen):
    brief: BriefContext
    outline: SpecOutline
    recipes: list[RecipeLite] = []
    research: list[ResearchAdditionLite] = []
    max_new_screens: int = 8


class C9bInput(Frozen):
    brief: BriefContext
    outline: SpecOutline
    research: list[ResearchAdditionLite] = []


class DesignBaseline(Frozen):
    direction: str
    color_tokens_light: list[KeyValue] = []
    radius: int = 12
    font_family: str | None = None


class C9cInput(Frozen):
    brief: BriefContext
    baseline: DesignBaseline
    brand_colors: list[str] = []
    brand_tone: list[str] = []
    research: list[ResearchAdditionLite] = []


C9aOutput = SpecProposals  # screens and navigation
C9bOutput = SpecProposals  # data, API and roles
C9cOutput = SpecProposals  # design tokens (set_design_token, add_imagery_rule)


# =========================================================================== C10 Screen briefs


class ScreenDraftLite(Frozen):
    id: str
    route: str
    recipe: str
    surface: str = "customer"
    purpose: str = ""
    sections: list[str] = []
    actions: list[str] = []  # "name -> target"
    copy_keys: list[str] = []


class C10Input(Frozen):
    brief: BriefContext
    screens: list[ScreenDraftLite]  # batch of 3 to 4
    recipes: list[RecipeLite] = []
    entities: list[OutlineEntity] = []
    operations: list[OutlineOperation] = []
    neighbors: list[OutlineScreen] = []
    research: list[ResearchAdditionLite] = []


C10Output = SpecProposals


# =========================================================================== C11 Write summary

SummaryKind = Literal["brief_sentence", "plan_summary"]


class C11Input(Frozen):
    kind: SummaryKind
    language: str = "en"
    app_name: str | None = None
    facts: list[str] = Field(description="Plain facts to summarise (slots, screens, flows)")
    max_words: int = 40


class C11Output(Frozen):
    text: str


# =========================================================================== C12 Critic

FindingKind = Literal["mismatch", "contradiction", "missing_flow", "missing_feature", "other"]
Severity = Literal["error", "warn", "info"]


class ValidatorFinding(Frozen):
    validator: str
    severity: Severity = "error"
    target_ids: list[str] = []
    message: str


class C12Input(Frozen):
    brief: BriefContext
    outline: SpecOutline
    validator_findings: list[ValidatorFinding] = []


class CriticFinding(Frozen):
    kind: FindingKind
    severity: Severity
    message: str
    target_ids: list[str] = []
    suggested_fix: str | None = None


class C12Output(Frozen):
    verdict: Literal["pass", "fix"]
    findings: list[CriticFinding]


# =========================================================================== C13 Repair


class BlockToRepair(Frozen):
    id: str
    kind: str
    text: str  # YAML of the block


class C13Input(Frozen):
    brief: BriefContext
    outline: SpecOutline
    findings: list[ValidatorFinding | CriticFinding]
    blocks: list[BlockToRepair] = []
    round: int = 1


C13Output = SpecProposals


# =========================================================================== C14 Interpret change

ChangeOpKind = Literal[
    "set_slot",
    "clear_slot",
    "toggle_feature",
    "add_screen",
    "remove_screen",
    "patch_screen",
    "set_copy",
    "toggle_research_item",
    "toggle_assumption",
    "other",
]


class ChangeOp(Frozen):
    kind: ChangeOpKind
    slot_id: SlotId | None = None
    value_text: str | None = None
    value_list: list[str] | None = None
    value_bool: bool | None = None
    target_id: str | None = Field(default=None, description="Screen/feature/research id or null")
    instruction: str | None = Field(default=None, description="Plain instruction for patch ops")


class C14Input(Frozen):
    message: str
    language: str = "en"
    state: SessionState
    gate_open: GateKind | None = None
    slots: list[SlotBrief] = []
    outline: SpecOutline | None = None
    selected_screen_id: str | None = None
    slot_catalog: list[SlotCatalogItem] = []
    source: Literal["text", "gate_comment"] = "text"


class C14Output(Frozen):
    understood: bool
    ops: list[ChangeOp] = []
    clarify_question: str | None = None
    is_approval: bool = False  # typed "ok/yes build it" rather than a change
    confidence: float = 0.8


# =========================================================================== misc


class SlotLevelOverride(Frozen):
    slot_id: SlotId
    level: SlotLevel
