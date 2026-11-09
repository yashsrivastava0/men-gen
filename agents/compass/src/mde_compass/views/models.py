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
    "look",
    "access",
    "integrations_data",
    "limits",
    "words",
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
    "look",
    "access",
    "integrations_data",
    "limits",
    "words",
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
