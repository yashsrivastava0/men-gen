"""Persistence records (spec section 11) as plain Pydantic models.

Repositories (``ports.py``) read/write these; ``mde_persistence`` maps them to tables.
Every record that belongs to a tenant carries ``tenant_id``; isolation is enforced in the
repository layer (a repository bound to tenant A can never see tenant B's rows).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from mde_contracts.brief import Brief
from mde_contracts.common import Frozen, Mode
from mde_contracts.engines import DiscoveryState
from mde_contracts.gates import PlanPhase, RunOrigin, SessionState
from mde_contracts.plan import PlanView
from mde_contracts.research import ResearchNote
from mde_contracts.slots import SlotRecord
from mde_contracts.spec import AppSpec

SpecStatus = Literal["draft", "approved", "handed_off", "superseded"]
JobKind = Literal["plan"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class SessionRecord(Frozen):
    id: str  # ses_...
    tenant_id: str
    owner_id: str
    app_id: str  # app_...
    mode: Mode = "guided"
    policy_id: str = "guided-default"
    state: SessionState = "DISCOVERING"
    plan_phase: PlanPhase | None = None
    origin: RunOrigin | None = None  # where the current/last PLANNING run started
    paused_from: SessionState | None = None
    language: str = "en"
    discovery: DiscoveryState = Field(default_factory=DiscoveryState)
    brief_version: int | None = None  # latest Brief
    approved_brief_version: int | None = None
    research_version: int | None = None
    spec_version: int | None = None  # latest draft/approved spec version
    handed_off_version: int | None = None
    queued_messages: list[str] = []  # typed during PLANNING; applied at Gate 2
    title: str | None = None
    version: int = 0  # optimistic concurrency token; incremented by every save
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BriefVersionRecord(Frozen):
    session_id: str
    tenant_id: str
    brief: Brief
    slots: dict[str, SlotRecord]  # snapshot the Brief was compiled from
    created_at: datetime | None = None


class ResearchNoteRecord(Frozen):
    session_id: str
    tenant_id: str
    note: ResearchNote
    created_at: datetime | None = None


class SpecVersionRecord(Frozen):
    """Approved versions are immutable: only ``status`` (approved -> handed_off) and ``git_sha``
    may change after approval; the repository must refuse any other update."""

    session_id: str
    tenant_id: str
    version: int
    hash: (
        str  # the file hash (blanked-hash rule, ADR 0006), equal to the hash embedded in spec_text
    )
    status: SpecStatus = "draft"
    spec_text: str
    spec: AppSpec
    plan: PlanView | None = None
    brief_version: int
    research_version: int | None = None
    git_sha: str | None = None
    created_at: datetime | None = None
    approved_at: datetime | None = None


class PlanJobPayload(Frozen):
    origin: RunOrigin
    start_phase: PlanPhase = "research"
    spec_version: int  # version number the run will create
    brief_version: int
    affected_sections: list[str] = ["all"]
    rerun_jobs: list[str] | None = None
    excluded_research_ids: list[str] = []
    change_ops_json: str | None = None  # serialised list[ChangeOp] when a change triggered it
    answer_slot_ids: list[str] = []  # slots answered after a needs_answer round
    via: Literal["tap", "typed"] = "typed"  # how the approval that started the run was given


class JobRecord(Frozen):
    id: str  # job_...
    session_id: str
    tenant_id: str
    kind: JobKind = "plan"
    state: JobState = "queued"
    phase: PlanPhase | None = None
    payload: PlanJobPayload
    attempts: int = 0
    max_attempts: int = 3
    lock_owner: str | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool = False
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
