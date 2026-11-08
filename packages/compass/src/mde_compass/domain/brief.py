"""The Brief (canonical ``brief.json``, schema version 1; prompt 5.2, ADR 0013).

Dict sections (``idea``, ``context``, ``audience``, ``experience``, ``access``, ``data``, ``constraints``)
map a catalog field name to a ``BriefItem`` so the content stays data-driven (hard rule 8): which keys
exist comes from ``catalog/slots.yaml`` (``brief_path``), not from this file. List sections hold typed
records, each carrying tier, provenance, confidence, status and history through ``ItemMeta``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_compass.domain.common import BriefItem, ItemMeta, Strict
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.research import ResearchRequest, SearchHints
from mde_compass.domain.situation import DiscoveryPlan, DomainHypothesis, SituationProfile

SCHEMA_VERSION = 1

# Content sections: what the hashes cover and what approval binds to.
SECTION_KEYS: tuple[str, ...] = (
    "idea",
    "context",
    "audience",
    "apps",
    "roles",
    "scope",
    "references",
    "experience",
    "access",
    "integrations",
    "data",
    "constraints",
    "glossary",
    "assumptions",
    "open_questions",
    "research_requests",
    "assets",
    "sources",
    "declined",
)
DICT_SECTIONS: tuple[str, ...] = (
    "idea",
    "context",
    "audience",
    "experience",
    "access",
    "data",
    "constraints",
)
LIST_SECTIONS: tuple[str, ...] = tuple(s for s in SECTION_KEYS if s not in DICT_SECTIONS)
ID_FIELD_BY_SECTION: dict[str, str] = {
    s: "id"
    for s in LIST_SECTIONS
    if s not in ("glossary", "references", "integrations", "declined")
}

FeatureStatus = Literal["in", "later", "out", "unsupported", "needs-engineering"]
Priority = Literal["must", "nice", "later"]
CapabilityTier = Literal["A", "B", "C"]
Theme = Literal["Business", "Users", "Features", "Look", "Access", "Integrations"]
Mode = Literal["guided", "enterprise"]


class BriefMeta(Strict):
    session_id: str = ""
    program_id: str = ""
    mode: Mode = "guided"
    situation_profile: SituationProfile | None = None
    plan: DiscoveryPlan | None = None
    hypothesis: DomainHypothesis | None = None
    locales: list[str] = Field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    depth_profile: str = "standard"
    degraded: bool = False  # handoff probes did not pass after the repair rounds
    probes: dict[str, bool] = Field(default_factory=dict)  # research|blueprint|design -> passed
    probe_rounds: int = 0  # repair rounds used so far (max config.gate.probe_repair_rounds)
    approvals: dict[str, str] = Field(
        default_factory=dict
    )  # contributor id -> brief hash approved (enterprise)


class AppRecord(ItemMeta):
    id: str
    name: str
    purpose: str = ""
    surface: str = "mobile"
    audience: str = ""
    roles: list[str] = Field(default_factory=list)


class RoleRecord(ItemMeta):
    id: str
    name_in_user_words: str
    goal: str = ""
    rough_permissions: list[str] = Field(default_factory=list)


class FeatureSize(Strict):
    screens: int = 0
    entities: int = 0
    integrations: int = 0


class FeatureRecord(ItemMeta):
    id: str
    name: str
    intent: str = ""
    apps: list[str] = Field(default_factory=list)
    family: str | None = None
    scope_status: FeatureStatus = (
        "in"  # in | later | out | unsupported | needs-engineering (item `status` is the slot state)
    )
    priority: Priority = "nice"
    source: str = ""
    size: FeatureSize = Field(default_factory=FeatureSize)
    capability_tier: CapabilityTier = "A"
    custom: bool = False  # outside families.yaml: flagged and confirmed


class ReferenceRecord(ItemMeta):
    label: str
    borrow: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class IntegrationRecord(ItemMeta):
    name: str
    preference: Literal["real_later", "mock", "undecided"] = "undecided"
    notes: str = ""


class GlossaryRecord(ItemMeta):
    term: str
    synonyms: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class AssumptionRecord(ItemMeta):
    id: str
    claim: str
    reason: str = ""
    basis: str = "pattern"  # evidence class: pattern | model_knowledge | assumed
    impact: Literal["low", "high"] = "low"
    theme: Theme = "Business"
    slot_id: str | None = None  # the slot this assumption stands in for
    decision: Literal["proposed", "accepted", "changed", "rejected"] = "proposed"
    what_changes: str = ""  # one line: what changes if the user edits it


class OpenQuestion(ItemMeta):
    id: str
    text: str
    blocking: bool = False
    owner: str = "user"
    slot_id: str | None = None


class AssetRecord(ItemMeta):
    id: str
    kind: str
    summary: str = ""
    pointer: str = ""
    consumers: list[str] = Field(default_factory=list)


class SourceRecord(ItemMeta):
    id: str  # SRC-###
    kind: str
    name: str
    sha256: str = ""
    coverage: float = 0.0


class DeclinedRecord(ItemMeta):
    what: str
    reason: str = ""
    alternative: str = ""


class ReadinessEntry(Strict):
    status: Literal["pass", "gaps"] = "gaps"
    gaps: list[str] = Field(default_factory=list)


class Readiness(Strict):
    research: ReadinessEntry = Field(default_factory=ReadinessEntry)
    blueprint: ReadinessEntry = Field(default_factory=ReadinessEntry)
    design: ReadinessEntry = Field(default_factory=ReadinessEntry)
    verify: ReadinessEntry = Field(default_factory=ReadinessEntry)


class Brief(Strict):
    """The product of Compass. ``version`` and ``ids`` are bookkeeping, outside the content hashes."""

    version: int = 0
    meta: BriefMeta = Field(default_factory=BriefMeta)
    idea: dict[str, BriefItem] = Field(default_factory=dict)
    context: dict[str, BriefItem] = Field(default_factory=dict)
    audience: dict[str, BriefItem] = Field(default_factory=dict)
    apps: list[AppRecord] = Field(default_factory=list)
    roles: list[RoleRecord] = Field(default_factory=list)
    scope: list[FeatureRecord] = Field(default_factory=list)
    references: list[ReferenceRecord] = Field(default_factory=list)
    experience: dict[str, BriefItem] = Field(default_factory=dict)
    access: dict[str, BriefItem] = Field(default_factory=dict)
    integrations: list[IntegrationRecord] = Field(default_factory=list)
    data: dict[str, BriefItem] = Field(default_factory=dict)
    constraints: dict[str, BriefItem] = Field(default_factory=dict)
    glossary: list[GlossaryRecord] = Field(default_factory=list)
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    research_requests: list[ResearchRequest] = Field(default_factory=list)
    search_hints: SearchHints = Field(default_factory=SearchHints)
    assets: list[AssetRecord] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    declined: list[DeclinedRecord] = Field(default_factory=list)
    readiness: Readiness = Field(default_factory=Readiness)
    ids: IdLedger = Field(default_factory=IdLedger)
    summary: str = ""  # one line (cmp.compile_text)
    pitch: str = ""
