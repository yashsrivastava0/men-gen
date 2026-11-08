"""Flow-owned view models returned to the API layer (read side) and the job payload helper."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from mde_contracts.calls import ChangeOp
from mde_contracts.cards import CardInstance
from mde_contracts.common import Frozen, GateKind, Mode
from mde_contracts.events import BriefPage, PlanPage
from mde_contracts.gates import ApprovalVia, GateStatus, PlanPhase, RunOrigin, SessionState


class SlotSummary(Frozen):
    value: str
    state: str
    source: str | None = None


class GateSnapshot(Frozen):
    """The gate that is open right now (derived from the state, never stored on its own)."""

    gate: GateKind
    version: int
    hash: str
    status: GateStatus
    buttons: list[str]
    page: BriefPage | PlanPage


class JobSummary(Frozen):
    id: str
    state: str
    phase: PlanPhase | None = None
    cancel_requested: bool = False


class SessionSnapshot(Frozen):
    """Where the user left off: state, pending card or open gate, slot summary (spec 2.5, resume)."""

    session_id: str
    app_id: str
    mode: Mode
    policy_id: str
    state: SessionState
    plan_phase: PlanPhase | None = None
    origin: RunOrigin | None = None
    paused_from: SessionState | None = None
    #: why a PAUSED session waits, when it is not the user's own pause ("budget": the cap was
    #: reached; an approver lifts it with ``resume`` once the budget allows planning again)
    paused_for: Literal["budget"] | None = None
    language: str = "en"
    pending_card: CardInstance | None = None
    gate: GateSnapshot | None = None
    brief_version: int | None = None
    approved_brief_version: int | None = None
    research_version: int | None = None
    spec_version: int | None = None
    handed_off_version: int | None = None
    slots: dict[str, SlotSummary] = Field(default_factory=dict)
    queued_messages: int = 0
    last_seq: int = 0
    allowed_transitions: list[str] = []
    active_job: JobSummary | None = None


class VersionInfo(Frozen):
    kind: Literal["brief", "spec"]
    version: int
    hash: str
    status: str
    git_sha: str | None = None
    created_at: datetime | None = None
    approved_at: datetime | None = None


class JobIntent(Frozen):
    """What a plan job was started for, carried in ``PlanJobPayload.change_ops_json``.

    The contract documents that field as a serialised ``list[ChangeOp]``; this flow-private form
    is a superset (a bare list is still accepted when reading).
    """

    ops: list[ChangeOp] = []
    answers: list[ChangeOp] = []  # replies to a ``needs_answer`` question (resume run)
    via: ApprovalVia = "typed"  # how the user asked (recorded on a light-change approval)
    #: True when the change was *derived* (from an uploaded document or an engine inference)
    #: instead of asked for by an explicit instruction or tap: such a change is never light.
    derived: bool = False


_OPS = TypeAdapter(list[ChangeOp])


def encode_intent(intent: JobIntent) -> str | None:
    if not intent.ops and not intent.answers:
        return None
    return intent.model_dump_json()


def decode_intent(raw: str | None) -> JobIntent:
    if not raw:
        return JobIntent()
    try:
        return JobIntent.model_validate_json(raw)
    except ValidationError:
        return JobIntent(ops=_OPS.validate_json(raw))
