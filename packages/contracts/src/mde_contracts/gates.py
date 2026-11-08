"""Session states, plan phases, gates and approvals (spec 2.1, 2.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from mde_contracts.common import Frozen, GateKind

SessionState = Literal[
    "DISCOVERING",
    "BRIEF_REVIEW",
    "PLANNING",
    "PLAN_REVIEW",
    "HANDED_OFF",
    "PAUSED",
    "STOPPED",
    "ARCHIVED",
]
MAIN_STATES: tuple[str, ...] = (
    "DISCOVERING",
    "BRIEF_REVIEW",
    "PLANNING",
    "PLAN_REVIEW",
    "HANDED_OFF",
)
PlanPhase = Literal["research", "build"]
RunOrigin = Literal["BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF"]
ApprovalDecision = Literal["approve", "change", "reject"]
ApprovalVia = Literal["tap", "typed"]
GateStatusName = Literal["open", "approved", "void", "changed"]

# Machine-readable transition names (the table lives in mde_flow).
TransitionName = Literal[
    "brief_complete",
    "approve_brief",
    "change_brief",
    "plan_ready",
    "needs_answer",
    "stop",
    "approve_plan",
    "change_plan",
    "change_after_handoff",
    "pause",
    "resume",
    "archive",
    "answer_given",  # resume after needs_answer
    "rediscover",  # restart discovery (also drops a suspended run)
]


class ApprovalRecord(Frozen):
    id: str  # apr_...
    session_id: str
    gate: GateKind
    version: int
    hash: str
    approver_id: str
    role: str | None = None
    decision: ApprovalDecision
    via: ApprovalVia = "tap"
    comment: str | None = None
    at: datetime | None = None


class GateStatus(Frozen):
    """Derived view of one open gate (never stored independently of its approvals)."""

    gate: GateKind
    version: int
    hash: str
    status: GateStatusName = "open"
    opened_at: datetime | None = None
    required_roles: list[str] = []
    approvals: list[ApprovalRecord] = []
    void_reason: str | None = None
