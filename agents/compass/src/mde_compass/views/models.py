"""BriefViewModel: the display-ready Brief page (prompt 6.9).

Built by deterministic code (``mde_compass.views.build``), so any front end renders it without logic:
the static test UI now, React later. Labels follow the session language (``catalog/strings``). Badges
carry text AND an icon, never colour alone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_compass.domain.common import Strict

BadgeKind = Literal["you_said", "from_document", "assumed", "typical", "suggestion"]
ValueKind = Literal["text", "list", "chips", "empty"]
BriefStatus = Literal["draft", "in_review", "approved"]
SectionId = Literal[
    "idea",
    "who",
    "apps_roles",
    "features",
    "structure",
    "look",
    "access",
    "integrations_data",
    "limits",
    "words",
    "details",
    "assumed",
    "unknown",
    "lookup",
    "sources",
    "cannot_build",
]
SECTION_IDS: tuple[str, ...] = (
    "idea",
    "who",
    "apps_roles",
    "features",
    "structure",
    "look",
    "access",
    "integrations_data",
    "limits",
    "words",
    "details",
    "assumed",
    "unknown",
    "lookup",
    "sources",
    "cannot_build",
)


class ViewBadge(Strict):
    kind: BadgeKind
    text: str  # "You said", "From your document", "Assumed", "Typical for apps like this", "Our suggestion"
    icon: str  # a plain Unicode symbol; the text always accompanies it


class ViewValue(Strict):
    kind: ValueKind = "empty"
    text: str = ""
    items: list[str] = Field(default_factory=list)


class ViewRow(Strict):
    id: str  # the path in the ChangeSet grammar: "idea.summary", "scope.FTR-004", "assumptions.ASM-002"
    label: str
    value: ViewValue
    badges: list[ViewBadge] = Field(default_factory=list)
    tier: Literal["Locked", "Default", "Open"] = "Open"
    editable: bool = True
    history_available: bool = False
    needs_review: bool = False  # e.g. an unreviewed high-impact assumption
    changed: bool = (
        False  # changed since the last approved version (or the last patch): shows "Updated"
    )
    group: str = ""  # Features: "<app> › <family>" grouping label
    status_chip: str = ""  # Features: in / later / out + priority, localized
    description: str = ""  # plain-language line from cmp.compile_text


class ViewSection(Strict):
    id: SectionId
    title: str
    rows: list[ViewRow] = Field(default_factory=list)
    reviewed: bool = False  # the user looked at it (approval progress)
    empty_text: str = ""
    count: int = 0


class ReadinessChip(Strict):
    consumer: Literal["research", "blueprint", "design"]
    label: str  # "Ready for Research" or "Research: 2 gaps"
    ready: bool
    gaps: list[str] = Field(default_factory=list)


class ViewHeader(Strict):
    title: str
    idea_line: str
    version: int
    status: BriefStatus
    status_label: str
    readiness: list[ReadinessChip] = Field(default_factory=list)
    reviewed: int = 0
    of_total: int = 0
    degraded: bool = False


class ViewFooter(Strict):
    primary_label: str  # "Looks right"
    secondary_label: str  # "Change something"
    approvable: bool
    blockers: list[str] = Field(default_factory=list)  # localized, one line each


class BriefViewModel(Strict):
    language: str = "en"
    session_id: str = ""
    header: ViewHeader
    sections: list[ViewSection]
    footer: ViewFooter
    brief_hash: str = ""
    section_hashes: dict[str, str] = Field(default_factory=dict)
    changed_since_approval: list[str] = Field(default_factory=list)  # section ids


# ----------------------------------------------------------------------------------------- the structure page
# The "Structure" tab of the page (ADR 0017): the same records the Brief holds, laid out for a person to review. Names
# of roles, features and apps are the person's own words; nothing here is a consumer view (it shows tier and decision).


class StructureEntityUse(Strict):
    entity_id: str
    entity: str
    use: str  # view | create | edit


class StructureScreenView(Strict):
    id: str
    name: str
    purpose: str = ""
    pattern: str = ""
    roles: list[str] = Field(default_factory=list)  # role names
    serves: list[str] = Field(default_factory=list)  # feature names
    entities: list[StructureEntityUse] = Field(default_factory=list)
    phase: Literal["core", "extended", "later"] = "extended"
    decision: Literal["proposed", "accepted"] = "proposed"
    tier: Literal["Locked", "Default", "Open"] = "Default"


class StructureStepView(Strict):
    screen_id: str
    screen: str
    action: str = ""


class StructureFlowView(Strict):
    id: str
    name: str
    role: str = ""
    goal: str = ""
    steps: list[StructureStepView] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    alternates: list[str] = Field(default_factory=list)
    modules: list[str] = Field(
        default_factory=list
    )  # names of the modules it passes through, in order
    decision: Literal["proposed", "accepted"] = "proposed"
    tier: Literal["Locked", "Default", "Open"] = "Default"


class StructureFieldView(Strict):
    key: str
    name: str = ""
    type: str = "text"
    required: bool = False
    ref: str = ""  # the name of the entity it points at
    values: list[str] = Field(default_factory=list)


class StructureAccessView(Strict):
    role_id: str
    role: str
    level: str  # none | view | create | edit | manage


class StructureEntityView(Strict):
    id: str
    key: str = ""
    name: str
    purpose: str = ""
    module: str = ""  # the owner module's name
    fields: list[StructureFieldView] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    access: list[StructureAccessView] = Field(default_factory=list)
    decision: Literal["proposed", "accepted"] = "proposed"
    tier: Literal["Locked", "Default", "Open"] = "Default"


class StructureModuleView(Strict):
    id: str
    name: str
    kind: str = ""
    purpose: str = ""
    apps: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    entry: str = ""  # the entry screen's name
    screens: list[StructureScreenView] = Field(default_factory=list)
    decision: Literal["proposed", "accepted"] = "proposed"
    tier: Literal["Locked", "Default", "Open"] = "Default"


class StructureMatrixRow(Strict):
    module_id: str
    module: str
    cells: list[int] = Field(
        default_factory=list
    )  # screens each role (in ``roles`` order) can open in this module


class StructureMatrix(Strict):
    roles: list[str] = Field(default_factory=list)  # role names, the columns
    rows: list[StructureMatrixRow] = Field(default_factory=list)


class StructureIssueView(Strict):
    code: str
    severity: Literal["error", "warn"]
    text: str
    ids: list[str] = Field(default_factory=list)


class StructureBandView(Strict):
    id: str  # small | medium | large ("" until a draft decided it)
    label: str = ""
    source: str = ""  # tier | document | user
    screens: list[int] = Field(default_factory=list)  # [low, high]
    open_upper: bool = False
    review: str = "summary"  # summary | soft | hard
    research_depth: str = ""


class StructureCounts(Strict):
    modules: int = 0
    screens: int = 0
    flows: int = 0
    entities: int = 0


class StructureViewModel(Strict):
    language: str = "en"
    session_id: str = ""
    enabled: bool = False  # the structure draft is on for this session
    drafted: bool = False
    band: StructureBandView
    counts: StructureCounts = Field(default_factory=StructureCounts)
    verdict: Literal["fits", "over", "under"] = "fits"
    suggested_band: str = ""  # a larger band the idea asked for ("" = none)
    unreviewed: int = 0  # modules not accepted yet
    review_blocks: bool = False  # the band makes unreviewed modules a Gate 1 blocker
    modules: list[StructureModuleView] = Field(default_factory=list)
    flows: list[StructureFlowView] = Field(default_factory=list)
    entities: list[StructureEntityView] = Field(default_factory=list)
    matrix: StructureMatrix = Field(default_factory=StructureMatrix)
    issues: list[StructureIssueView] = Field(default_factory=list)
