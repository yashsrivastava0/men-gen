"""The Plan view (spec 2.3, Gate 2): a typed model so a future UI can render it."""

from __future__ import annotations

from typing import Literal

from mde_contracts.brief import AssumedItem
from mde_contracts.common import FlowId, Frozen, ResearchRefId, ScreenId
from mde_contracts.gates import ApprovalDecision
from mde_contracts.research import CompliancePointer, SourceRef
from mde_contracts.spec import BacklogItem


class ScreenMapGroup(Frozen):
    flow_id: FlowId | None
    flow_name: str
    screen_ids: list[ScreenId]
    screen_titles: list[str]
    count: int


class LookPreview(Frozen):
    direction: str
    swatches_light: dict[str, str]
    swatches_dark: dict[str, str]
    radius: int
    font_sample: str | None = None


class PlainEntity(Frozen):
    id: str
    plain_name: str
    fields_plain: list[str]


class ResearchAdditionView(Frozen):
    id: ResearchRefId
    title: str
    kind: str
    why: str  # "seen in most similar apps" in Guided; full detail in Enterprise
    sources: list[SourceRef] = []
    included: bool = True
    verified: bool = False


class IntegrationStatus(Frozen):
    id: str
    name: str
    mode: Literal["mock", "adapter", "real"]
    needs_dev_build: bool = False


class RoleView(Frozen):
    id: str
    name: str
    sees: list[str]  # screen titles in plain words


class PlanDiff(Frozen):
    since: Literal["gate1", "previous_version"]
    added_ids: list[str] = []
    removed_ids: list[str] = []
    changed_ids: list[str] = []
    lines: list[str] = []  # plain-language lines


class ApproverStatus(Frozen):
    role: str
    user_id: str | None = None
    decision: ApprovalDecision | None = None


class EnterpriseExtras(Frozen):
    approvers: list[ApproverStatus] = []
    comments: list[str] = []
    full_file_view: bool = False


class PlanView(Frozen):
    spec_version: int
    spec_hash: str
    language: str = "en"
    summary: str
    screen_count: int
    screen_map: list[ScreenMapGroup]
    look: LookPreview
    data: list[PlainEntity]
    research_status: Literal["ok", "partial", "unavailable", "skipped"] = "skipped"
    research_note: str | None = None  # "Research was skipped: ..." when relevant
    research_added: list[ResearchAdditionView] = []
    compliance: list[CompliancePointer] = []  # pointers only; not legal advice
    integrations: list[IntegrationStatus] = []
    roles: list[RoleView] = []
    diff_since_gate1: PlanDiff | None = None
    assumptions: list[AssumedItem] = []
    backlog: list[BacklogItem] = []
    enterprise: EnterpriseExtras | None = None
