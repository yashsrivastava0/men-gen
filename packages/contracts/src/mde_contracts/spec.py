"""Spec block models (spec section 6) and the ``AppSpec`` aggregate.

The rendered ``APP_SPEC.md`` is Markdown for people plus fenced YAML blocks for machines
(```yaml id=SCR-014 kind=screen```). Each block kind below maps to one block; ``screen`` and
``flow`` repeat. ``mde_spec_builder`` renders (deterministically) and ``mde_spec_loader`` parses.

Field naming note: the screen's ``copy`` map is called ``texts`` here (``copy`` would shadow a
Pydantic attribute); the renderer emits it under the YAML key ``copy``.
"""

from __future__ import annotations

from typing import Literal

from mde_contracts.common import (
    SPEC_SCHEMA_ID,
    ApiId,
    BuildStepId,
    EntityId,
    FlowId,
    Frozen,
    IntegrationId,
    Mode,
    ResearchRefId,
    RoleId,
    ScreenId,
)
from mde_contracts.research import SourceRef
from mde_core.hashing import hash_model

BlockKind = Literal[
    "header",
    "app",
    "design",
    "navigation",
    "data",
    "api",
    "roles",
    "integrations",
    "behavior",
    "screen",
    "flow",
    "build_sequence",
    "acceptance",
    "assumptions",
    "revision",
    "backlog",
]

# Fixed block ids of the singleton blocks; screens use SCR-###, flows FLW-###.
SINGLETON_BLOCK_IDS: dict[str, str] = {
    "header": "HEADER",
    "app": "APP",
    "design": "DESIGN",
    "navigation": "NAV",
    "data": "DATA",
    "api": "API",
    "roles": "ROLES",
    "integrations": "INTEGRATIONS",
    "behavior": "BEHAVIOR",
    "build_sequence": "BUILD",
    "acceptance": "ACCEPT",
    "assumptions": "ASSUME",
    "revision": "REV",
    "backlog": "BACKLOG",
}

# Fixed section order of APP_SPEC.md (spec 6.2).
SECTION_ORDER: tuple[str, ...] = (
    "header",
    "summary",
    "app",
    "design",
    "navigation",
    "data",
    "api",
    "roles",
    "integrations",
    "behavior",
    "screens",
    "flows",
    "build_sequence",
    "acceptance",
    "assumptions",
    "revision",
)

SpecItemSource = Literal["brief", "default", "research", "custom"]
Surface = str  # "customer" | "staff" | "admin" | ... (data-driven by the Domain Pack)

# --------------------------------------------------------------------------- app / design


class AppBlock(Frozen):
    name: str
    purpose: str
    audience: str
    platforms: list[Literal["ios", "android"]] = ["ios", "android"]
    tone: str = "friendly"
    language: str = "en"


class TypeStyle(Frozen):
    size: int
    weight: int = 400
    line_height: float = 1.3


class DesignTokens(Frozen):
    colors_light: dict[str, str]  # token name -> hex
    colors_dark: dict[str, str]
    type_scale: dict[str, TypeStyle]
    spacing: list[int]
    radius: dict[str, int]
    elevation: dict[str, str]
    motion: dict[str, str]


class DesignBlock(Frozen):
    direction: str  # theme preset id
    tokens: DesignTokens
    nav_style: str | None = None
    imagery_rules: list[str] = []
    component_variants: dict[str, list[str]] = {}


# --------------------------------------------------------------------------- navigation


class NavNode(Frozen):
    id: str  # "NAV-001"
    route: str
    kind: Literal["tabs", "stack", "modal", "screen", "auth_gate"]
    screen: ScreenId | None = None
    label: str | None = None
    auth: str = "public"  # public | authenticated | ROLE-x
    children: list[NavNode] = []


class NavigationBlock(Frozen):
    root: NavNode


# --------------------------------------------------------------------------- data / api / roles

FieldType = Literal[
    "string", "text", "int", "number", "bool", "datetime", "date", "money", "enum", "ref", "image",
    "json",
]  # fmt: skip


class FieldDef(Frozen):
    name: str
    type: FieldType = "string"
    required: bool = True
    ref: EntityId | None = None  # for type == "ref"
    enum: str | None = None  # EnumDef.name for type == "enum"
    description: str | None = None


class Transition(Frozen):
    source: str  # state it leaves ("from" is a keyword; rendered as `from`)
    target: str
    on: str  # event / action name


class StateMachine(Frozen):
    field: str
    states: list[str]
    transitions: list[Transition] = []


class Entity(Frozen):
    id: EntityId
    name: str
    plain_name: str  # everyday noun shown at Gate 2 ("Orders")
    fields: list[FieldDef]
    state_machine: StateMachine | None = None
    seed_guidance: str | None = None
    source: SpecItemSource = "default"


class EnumDef(Frozen):
    name: str
    values: list[str]


class DataBlock(Frozen):
    entities: list[Entity]
    enums: list[EnumDef] = []
    seed_guidance: str | None = None


class ApiOperation(Frozen):
    id: ApiId
    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    request_entity: EntityId | None = None
    response_entity: EntityId | None = None
    response_many: bool = False
    auth: str = "public"
    roles: list[RoleId] = []
    notes: str | None = None
    source: SpecItemSource = "default"


class ApiBlock(Frozen):
    operations: list[ApiOperation]
    mock_guidance: str | None = None


class Role(Frozen):
    id: RoleId
    name: str
    description: str = ""
    surface: Surface = "customer"


class Permission(Frozen):
    role: RoleId
    screens: list[str]  # ScreenIds or "*"
    operations: list[str]  # ApiIds or "*"


class RolesBlock(Frozen):
    roles: list[Role]
    permissions: list[Permission] = []


# --------------------------------------------------------------------------- integrations / behavior


class Integration(Frozen):
    id: IntegrationId
    name: str
    purpose: str
    mode: Literal["mock", "adapter", "real"] = "mock"
    provider: str | None = None
    notes: str | None = None
    source: SpecItemSource = "default"
    research_ref: ResearchRefId | None = None
    needs_dev_build: bool = False


class IntegrationsBlock(Frozen):
    items: list[Integration]


class BehaviorBlock(Frozen):
    offline: str = "basic cache"
    locale: str = "en"
    deep_links: list[str] = []
    notifications: list[str] = []
    analytics_events: list[str] = []
    permission_prompts: list[str] = []
    error_conventions: list[str] = []
    accessibility: list[str] = []


# --------------------------------------------------------------------------- screens / flows


class DataBinding(Frozen):
    bind: str  # "API-getProduct(id)" or "ENT-Product.name"
    show: list[str] = []


class ScreenAction(Frozen):
    name: str
    calls: ApiId | None = None
    go: str | None = None  # ScreenId or sheet id
    on_success: str | None = None


class ScreenBlock(Frozen):
    id: ScreenId
    route: str
    surface: Surface = "customer"
    recipe: str
    purpose: str
    title: str | None = None  # plain-language screen title (else derived from texts/route)
    auth: str = "public"
    flow: FlowId | None = None
    data: list[DataBinding] = []
    sections: list[str] = []
    actions: list[ScreenAction] = []
    states: dict[str, dict[str, str]] = {}  # only deviations from the recipe
    texts: dict[str, str] = {}  # rendered as YAML key `copy`
    checks: list[str] = []
    notes: str | None = None
    source: SpecItemSource = "default"
    research_ref: ResearchRefId | None = None
    locked: bool = False  # hand-edited downstream: skipped on regeneration


class FlowBlock(Frozen):
    id: FlowId
    name: str
    primary: bool = False
    screens: list[ScreenId]
    start: ScreenId
    end: ScreenId
    purpose: str | None = None


# --------------------------------------------------------------------------- build / acceptance


class ParallelGroup(Frozen):
    group: str
    screens: list[ScreenId]
    owns: list[str]  # path globs the group may write


class BuildStep(Frozen):
    id: BuildStepId
    name: str
    load: list[str] = []  # block ids to load (APP, DESIGN, NAV, ...)
    gate: str | None = None
    flow: FlowId | None = None
    screens: list[ScreenId] = []
    stop_for_feedback: bool = False
    parallel: list[ParallelGroup] = []


class BuildSequence(Frozen):
    steps: list[BuildStep]


class AcceptanceBlock(Frozen):
    checks: list[str] = []


# --------------------------------------------------------------------------- assumptions / revision


class AssumptionItem(Frozen):
    slot_id: str | None = None
    label: str
    value_text: str
    impact: str  # one line: what this assumption changes in the app


class ResearchAddition(Frozen):
    id: ResearchRefId
    title: str
    kind: str
    sources: list[SourceRef] = []
    included: bool = True
    affects: list[str] = []  # spec ids touched by this addition
    verified: bool = False


class AssumptionsBlock(Frozen):
    assumptions: list[AssumptionItem] = []
    research_additions: list[ResearchAddition] = []
    research_status: Literal["ok", "partial", "unavailable", "skipped"] = "skipped"
    research_note: str | None = None  # e.g. "research: unavailable (budget)"


class RevisionBlock(Frozen):
    version: int
    previous_version: int | None = None
    changed_ids: list[str] = []


class BacklogItem(Frozen):
    title: str
    reason: str = "phase 2"
    screens_hint: list[str] = []


class SpecHeader(Frozen):
    spec_version: int
    schema_id: str = SPEC_SCHEMA_ID
    app_id: str
    mode: Mode = "guided"
    stack_profile: str = "expo-managed"
    inherits: list[str] = []
    language: str = "en"
    hash: str = ""  # sha256 of the rendered file with this field blanked (see renderer)
    brief_hash: str
    research_note_hash: str | None = None


class AppSpec(Frozen):
    """The Spec Draft: structured truth during the conversation; ``APP_SPEC.md`` is its render."""

    header: SpecHeader
    summary: str  # plain-language PRD view (C11 or deterministic template)
    app: AppBlock
    design: DesignBlock
    navigation: NavigationBlock
    data: DataBlock
    api: ApiBlock
    roles: RolesBlock
    integrations: IntegrationsBlock
    behavior: BehaviorBlock
    screens: list[ScreenBlock]
    flows: list[FlowBlock]
    build: BuildSequence
    acceptance: AcceptanceBlock
    assumptions: AssumptionsBlock
    revision: RevisionBlock
    backlog: list[BacklogItem] = []

    def compute_content_hash(self) -> str:
        """Hash of the structured content (header.hash blanked); the *file* hash is the sha256
        of the rendered text and is what approvals bind to."""
        return hash_model(
            self.model_copy(update={"header": self.header.model_copy(update={"hash": ""})})
        )


NavNode.model_rebuild()
