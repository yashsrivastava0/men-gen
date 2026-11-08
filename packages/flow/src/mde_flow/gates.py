"""Gates and approvals: the pure rules of spec 2.2 (the flow service persists, this module decides).

The eight gate rules and where they are enforced:

1. **One output, two buttons.** :func:`open_gate` builds a :class:`GateEvent` with exactly one
   page and the buttons from :func:`gate_buttons` ("Looks right" / "Change something";
   Enterprise: "Send for approval").
2. **Approval bound to the hash; void on change.** :func:`check_binding` raises
   :class:`HashMismatch` for any version/hash that is not the current one;
   :func:`derive_status` counts only approvals at exactly the current (version, hash), so a new
   version starts with no approvals; the gate re-opens with a diff and ``single_button=True``.
3. **Typed approval only with a gate open and a confident engine.** :func:`typed_approval_counts`;
   the approval records ``via="typed"``.
4. **A change the user asks for is its own approval of that change.** The service applies a
   requested change at once (no confirm step); post-handoff light changes need no gate, and
   anything the engine added or repaired escalates (see ``dependency_map.classify_post_handoff``).
5. **No step before its gate.** The service starts research/build only through
   ``machine.apply("approve_brief")`` and the frontend only after :func:`assert_handoff_allowed`.
6. **Silence never approves.** Nothing in this module approves on its own: an approval needs a
   decision with an approver; time never creates one. Reminders are a port (:class:`ReminderPort`).
7. **Enterprise approvers.** Required *roles* come from ``policy.gates.<gate>.approvers``;
   :func:`is_complete` needs every required role's latest decision on the same hash to be
   ``approve``. A rejection's comments become change requests (the service feeds them to the
   discovery engine). **Separation of duties**: when a gate needs more than one role, each role is
   approved by a different person, and the requester only ever stands in for the ``requester`` role.
   The ``requester`` role belongs to the session owner alone: a ``requester`` claimed in a caller's
   roles is ignored.
8. **Approved versions never change silently.** :func:`assert_handoff_allowed` refuses a spec whose
   hash is not the sha256 of its text; the service only ever creates *new* versions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from mde_contracts.brief import Brief
from mde_contracts.common import GateKind, Mode, Principal
from mde_contracts.engines import DiscoveryResult
from mde_contracts.events import BriefPage, GateEvent, PlanPage
from mde_contracts.gates import ApprovalDecision, ApprovalRecord, ApprovalVia, GateStatus
from mde_contracts.plan import ApproverStatus, EnterpriseExtras, PlanView
from mde_contracts.policy import Policy
from mde_core.errors import GateError, HashMismatch
from mde_flow import messages
from mde_spec_loader.hashing import compute_spec_hash

REQUESTER = "requester"  # the session owner implicitly holds this role
#: Marker on the approval recorded for a light post-handoff change (the user's request is the
#: approval, spec 3.6). It is an honest record: one requester approval, never forged role approvals.
LIGHT_CHANGE_COMMENT = "light_change: the user's request is the approval"

# --------------------------------------------------------------------------- policy helpers


def required_roles(policy: Policy, gate: GateKind) -> list[str]:
    """Approver roles that must all approve the same hash (spec 2.2 rule 7)."""
    rule = policy.gates.brief if gate == "brief" else policy.gates.plan
    return list(rule.approvers) or [REQUESTER]


def gate_buttons(mode: Mode, *, single_button: bool) -> list[str]:
    primary = messages.BTN_APPROVE_ENTERPRISE if mode == "enterprise" else messages.BTN_APPROVE
    return [primary] if single_button else [primary, messages.BTN_CHANGE]


def held_roles(principal: Principal, owner_id: str) -> frozenset[str]:
    """Roles a principal acts under.

    ``requester`` is the **session owner's** role and nobody else's: it is added for the owner and
    dropped from everyone's claimed roles (a header is not a reason to be the requester). The other
    roles are whatever the authenticated caller carries.
    """
    roles = {r for r in principal.roles if r != REQUESTER}
    if principal.user_id == owner_id:
        roles.add(REQUESTER)
    return frozenset(roles)


# --------------------------------------------------------------------------- derived status


def latest_by_role(
    approvals: Sequence[ApprovalRecord], *, version: int, hash: str
) -> dict[str, ApprovalRecord]:
    """Each role's most recent decision on exactly (version, hash); insertion order wins."""
    out: dict[str, ApprovalRecord] = {}
    for rec in approvals:
        if rec.version == version and rec.hash == hash and rec.role is not None:
            out[rec.role] = rec
    return out


def approved_roles(approvals: Sequence[ApprovalRecord], *, version: int, hash: str) -> list[str]:
    latest = latest_by_role(approvals, version=version, hash=hash)
    return [role for role, rec in latest.items() if rec.decision == "approve"]


def is_complete(
    roles: Sequence[str], approvals: Sequence[ApprovalRecord], *, version: int, hash: str
) -> bool:
    """True when every required role's latest decision on this exact hash is an approval, and,
    when more than one role is required, no person stands behind two of them (separation of duties:
    the approver of each role is a different user)."""
    if not roles:
        return False  # a gate with no approver can never be satisfied silently
    latest = latest_by_role(approvals, version=version, hash=hash)
    for role in roles:
        rec = latest.get(role)
        if rec is None or rec.decision != "approve":
            return False
    distinct_roles = set(roles)
    if len(distinct_roles) > 1:
        approvers = [latest[role].approver_id for role in distinct_roles]
        return len(set(approvers)) == len(approvers)
    return True


def derive_status(
    *,
    gate: GateKind,
    version: int,
    hash: str,
    approvals: Sequence[ApprovalRecord],
    roles: Sequence[str],
    opened_at: datetime | None = None,
) -> GateStatus:
    """The gate's status, derived from approvals at the current hash only (never stored)."""
    here = [a for a in approvals if a.version == version and a.hash == hash]
    return GateStatus(
        gate=gate,
        version=version,
        hash=hash,
        status="approved" if is_complete(roles, approvals, version=version, hash=hash) else "open",
        opened_at=opened_at,
        required_roles=list(roles),
        approvals=here,
    )


def void_gate(status: GateStatus, reason: str) -> GateStatus:
    """The previous version's gate after a change: its approvals no longer count."""
    return status.model_copy(update={"status": "void", "void_reason": reason})


def check_binding(*, current_version: int, current_hash: str, version: int, hash: str) -> None:
    """Rule 2: an approval is bound to a version *and* its hash. Anything else is stale."""
    if version != current_version or hash != current_hash:
        raise HashMismatch(
            "that version is no longer the current one",
            details={
                "current_version": current_version,
                "current_hash": current_hash,
                "attempted_version": version,
                "attempted_hash": hash,
            },
        )


def typed_approval_counts(*, gate_open: GateKind | None, result: DiscoveryResult) -> bool:
    """Rule 3: typed 'yes' counts only when a gate is open AND the engine is confident.

    Text that also carries a change ("looks fine but make it blue") is a change, not an approval.
    """
    return (
        gate_open is not None
        and result.approval_confident
        and not result.change_ops
        and result.intent in (None, "approve")
    )


# --------------------------------------------------------------------------- recording a decision

DecisionKind = Literal[
    "approved", "pending", "duplicate", "submitted", "change_requested", "rejected"
]


@dataclass(frozen=True)
class DecisionOutcome:
    kind: DecisionKind
    record: ApprovalRecord | None  # to persist (None: nothing new to store)
    status: GateStatus  # status of the gate *after* this decision
    complete: bool  # every required role has approved this exact hash
    role: str | None = None
    comments: tuple[str, ...] = field(default=())  # each becomes a change request


def split_comments(comment: str | None) -> tuple[str, ...]:
    """A rejection's comment becomes one change request per non-empty line (spec 2.2 rule 7)."""
    if not comment:
        return ()
    return tuple(line.strip() for line in comment.splitlines() if line.strip())


def record_decision(
    *,
    session_id: str,
    gate: GateKind,
    gate_open: bool,
    current_version: int,
    current_hash: str,
    decision: ApprovalDecision,
    version: int,
    hash: str,
    via: ApprovalVia,
    principal: Principal,
    owner_id: str,
    mode: Mode,
    roles: Sequence[str],
    existing: Sequence[ApprovalRecord],
    new_id: Callable[[str], str],
    at: datetime,
    comment: str | None = None,
    requested_role: str | None = None,
    approver_id: str | None = None,
    opened_at: datetime | None = None,
) -> DecisionOutcome:
    """Validate one decision against the open gate and say what to persist.

    Raises :class:`GateError` when no gate is open or the caller may not decide, and
    :class:`HashMismatch` when ``(version, hash)`` is not the current one. Never mutates anything.
    """
    if not gate_open:
        raise GateError("there is no open gate to decide on", details={"gate": gate})
    check_binding(
        current_version=current_version, current_hash=current_hash, version=version, hash=hash
    )
    if approver_id is not None and approver_id != principal.user_id:
        raise GateError("approver_id must be the authenticated user", details={"gate": gate})

    held = held_roles(principal, owner_id)
    held_here = [r for r in roles if r in held]
    eligible = held_here
    is_owner = principal.user_id == owner_id
    multi = len(set(roles)) > 1
    if multi and is_owner:
        # separation of duties: on a gate with several approvers the requester is only the
        # requester; the other roles are decided by other people
        eligible = [r for r in held_here if r == REQUESTER]
    if requested_role is not None and requested_role not in eligible:
        if requested_role in held_here:
            raise GateError(
                f"the requester cannot also approve as {requested_role!r}",
                details={"gate": gate, "role": requested_role},
            )
        raise GateError(
            f"you cannot act as {requested_role!r} on this gate",
            details={"gate": gate, "role": requested_role},
        )

    def status_with(extra: ApprovalRecord | None) -> GateStatus:
        every = [*existing, extra] if extra is not None else list(existing)
        return derive_status(
            gate=gate, version=current_version, hash=current_hash, approvals=every, roles=roles,
            opened_at=opened_at,
        )  # fmt: skip

    def make(role: str | None, dec: ApprovalDecision) -> ApprovalRecord:
        return ApprovalRecord(
            id=new_id("apr"),
            session_id=session_id,
            gate=gate,
            version=current_version,
            hash=current_hash,
            approver_id=principal.user_id,
            role=role,
            decision=dec,
            via=via,
            comment=comment,
            at=at,
        )

    if decision == "approve":
        if not eligible:
            if mode == "enterprise" and principal.user_id == owner_id:
                # "Send for approval": the requester submits; the named approvers decide.
                return DecisionOutcome("submitted", None, status_with(None), False)
            raise GateError("you are not an approver for this gate", details={"gate": gate})
        done = set(approved_roles(existing, version=current_version, hash=current_hash))
        if multi:
            # one person, one role: whoever already approved this hash for a role cannot also
            # approve for another one
            latest = latest_by_role(existing, version=current_version, hash=current_hash)
            mine = {
                role
                for role, rec in latest.items()
                if rec.decision == "approve" and rec.approver_id == principal.user_id
            }
            if mine:
                if requested_role is not None and requested_role not in mine:
                    raise GateError(
                        "one person cannot approve for more than one role",
                        details={"gate": gate, "role": requested_role},
                    )
                eligible = [r for r in eligible if r in mine]
                if not eligible:
                    raise GateError("you are not an approver for this gate", details={"gate": gate})
        role = requested_role or next((r for r in eligible if r not in done), eligible[0])
        if role in done:
            status = status_with(None)
            return DecisionOutcome("duplicate", None, status, status.status == "approved", role)
        rec = make(role, "approve")
        status = status_with(rec)
        kind: DecisionKind = "approved" if status.status == "approved" else "pending"
        return DecisionOutcome(kind, rec, status, status.status == "approved", role)

    if decision == "reject":
        if not eligible:
            raise GateError("only a required approver can reject", details={"gate": gate})
        role = requested_role or eligible[0]
        rec = make(role, "reject")
        return DecisionOutcome(
            "rejected", rec, status_with(rec), False, role, split_comments(comment)
        )

    # "change": the owner or any approver asks for something different.
    role = requested_role or (eligible[0] if eligible else None)
    rec = make(role, "change")
    return DecisionOutcome(
        "change_requested", rec, status_with(rec), False, role, split_comments(comment)
    )


# --------------------------------------------------------------------------- hand-off guard


def assert_handoff_allowed(
    *,
    version: int,
    spec_hash: str,
    spec_text: str,
    roles: Sequence[str],
    approvals: Sequence[ApprovalRecord],
) -> None:
    """The last line of defence before ``spec_ready`` (hard rule 6; spec 2.2 rules 2, 5, 8).

    Raises :class:`GateError` unless the spec text really hashes to ``spec_hash`` (the file hash
    rule of ``mde_spec_loader.hashing``: sha256 of the text with its own ``hash:`` value blanked,
    which is the plain sha256 for text without a hash line) and every
    required role approved exactly this (version, hash) (each by a different person when several
    roles are required). There is no other way through: a light post-handoff change is covered by
    the requester's own recorded approval only because the requester **is** the whole gate then
    (``roles == ["requester"]``, see ``dependency_map.classify_post_handoff``).
    """
    if compute_spec_hash(spec_text) != spec_hash:
        raise GateError(
            "the spec text does not match its hash", details={"version": version, "hash": spec_hash}
        )
    if is_complete(roles, approvals, version=version, hash=spec_hash):
        return
    raise GateError(
        "hand-off needs an approval of the current hash from every required approver",
        details={"version": version, "hash": spec_hash, "roles": list(roles)},
    )


# --------------------------------------------------------------------------- opening a gate


def approver_statuses(
    roles: Sequence[str], approvals: Sequence[ApprovalRecord], *, version: int, hash: str
) -> list[ApproverStatus]:
    latest = latest_by_role(approvals, version=version, hash=hash)
    out: list[ApproverStatus] = []
    for role in roles:
        rec = latest.get(role)
        out.append(
            ApproverStatus(
                role=role,
                user_id=rec.approver_id if rec else None,
                decision=rec.decision if rec else None,
            )
        )
    return out


def plan_with_enterprise_extras(
    plan: PlanView,
    *,
    mode: Mode,
    policy: Policy,
    approvals: Sequence[ApprovalRecord],
    roles: Sequence[str],
) -> PlanView:
    """Fill the Enterprise extras of the Plan view (approver status, comments, full-file view)."""
    if mode != "enterprise" and len(roles) <= 1:
        return plan
    comments = [
        a.comment
        for a in approvals
        if a.gate == "plan" and a.comment and a.decision in ("reject", "change")
    ]
    extras = EnterpriseExtras(
        approvers=approver_statuses(
            roles, approvals, version=plan.spec_version, hash=plan.spec_hash
        ),
        comments=comments,
        full_file_view=policy.gates.plan.full_file_view,
    )
    return plan.model_copy(update={"enterprise": extras})


def open_gate(
    *,
    gate: GateKind,
    version: int,
    hash: str,
    page: BriefPage | PlanPage,
    mode: Mode,
    roles: Sequence[str],
    approvals: Sequence[ApprovalRecord],
    session_id: str,
    event_id: str,
    at: datetime,
    diff_lines: Sequence[str] = (),
    in_reply_to: str | None = None,
) -> tuple[GateEvent, GateStatus]:
    """Build the gate event (one page, buttons, diff) and the gate's derived status.

    A re-opened gate (``diff_lines`` given) shows the diff and a single button (rule 2). The
    approved roles shown are only those that approved *this* hash.
    """
    status = derive_status(
        gate=gate, version=version, hash=hash, approvals=approvals, roles=roles, opened_at=at
    )
    reopened = bool(diff_lines)
    event = GateEvent(
        event_id=event_id,
        session_id=session_id,
        at=at,
        in_reply_to=in_reply_to,
        gate=gate,
        version=version,
        hash=hash,
        page=page,
        buttons=gate_buttons(mode, single_button=reopened),
        required_roles=list(roles),
        approved_roles=approved_roles(approvals, version=version, hash=hash),
        diff_lines=list(diff_lines),
        single_button=reopened,
    )
    return event, status


def brief_page(brief: Brief) -> BriefPage:
    return BriefPage(brief=brief)


def plan_page(plan: PlanView) -> PlanPage:
    return PlanPage(plan=plan)


# --------------------------------------------------------------------------- reminders (port)


class ReminderPort(Protocol):
    """Enterprise reminders to the named approvers while a gate stays open (spec 2.2 rule 6).

    Reminders never approve anything: they only nudge. ``NoopReminders`` records the calls.
    """

    async def schedule(
        self,
        *,
        tenant_id: str,
        session_id: str,
        gate: GateKind,
        version: int,
        hash: str,
        roles: Sequence[str],
        after_hours: int,
    ) -> None: ...

    async def cancel(self, *, tenant_id: str, session_id: str, gate: GateKind) -> None: ...


@dataclass(frozen=True)
class ScheduledReminder:
    tenant_id: str
    session_id: str
    gate: GateKind
    version: int
    hash: str
    roles: tuple[str, ...]
    after_hours: int


class NoopReminders:
    """Records reminder requests; sends nothing."""

    def __init__(self) -> None:
        self.scheduled: list[ScheduledReminder] = []
        self.cancelled: list[tuple[str, str, GateKind]] = []

    async def schedule(
        self,
        *,
        tenant_id: str,
        session_id: str,
        gate: GateKind,
        version: int,
        hash: str,
        roles: Sequence[str],
        after_hours: int,
    ) -> None:
        self.scheduled.append(
            ScheduledReminder(tenant_id, session_id, gate, version, hash, tuple(roles), after_hours)
        )

    async def cancel(self, *, tenant_id: str, session_id: str, gate: GateKind) -> None:
        self.cancelled.append((tenant_id, session_id, gate))


def reminder_hours(policy: Policy, gate: GateKind) -> int | None:
    rule = policy.gates.brief if gate == "brief" else policy.gates.plan
    return rule.reminder_hours
