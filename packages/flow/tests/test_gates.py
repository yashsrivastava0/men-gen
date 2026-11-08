"""The eight gate rules of spec 2.2, one section each, on the pure functions of ``mde_flow.gates``
(the end-to-end versions live in ``test_scenarios.py``)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mde_contracts.common import Principal
from mde_contracts.engines import DiscoveryResult, DiscoveryState
from mde_contracts.events import BriefPage, PlanPage
from mde_contracts.gates import ApprovalRecord
from mde_contracts.policy import GateRule, Policy, PolicyGates
from mde_core.errors import GateError, HashMismatch
from mde_core.hashing import sha256_hex
from mde_core.ids import SequentialIds
from mde_flow import gates
from mde_flow.testing.builders import (
    enterprise_policy,
    guided_policy,
    make_brief,
    make_plan,
    make_spec,
    render_spec_text,
)

NOW = datetime(2026, 11, 8, tzinfo=UTC)
OWNER = Principal(tenant_id="t", user_id="owner", roles=[])
PO = Principal(tenant_id="t", user_id="pat", roles=["client_product_owner"])
DL = Principal(tenant_id="t", user_id="dana", roles=["delivery_lead"])
ENT_ROLES = ["client_product_owner", "delivery_lead"]


def decide(
    decision: str = "approve",
    *,
    who: Principal = OWNER,
    roles: list[str] | None = None,
    existing: list[ApprovalRecord] | None = None,
    version: int = 1,
    hash: str = "h1",
    current_version: int = 1,
    current_hash: str = "h1",
    via: str = "tap",
    mode: str = "guided",
    gate_open: bool = True,
    comment: str | None = None,
    role: str | None = None,
    approver_id: str | None = None,
    ids: SequentialIds | None = None,
) -> gates.DecisionOutcome:
    return gates.record_decision(
        session_id="ses_1",
        gate="plan",
        gate_open=gate_open,
        current_version=current_version,
        current_hash=current_hash,
        decision=decision,  # type: ignore[arg-type]
        version=version,
        hash=hash,
        via=via,  # type: ignore[arg-type]
        principal=who,
        owner_id="owner",
        mode=mode,  # type: ignore[arg-type]
        roles=roles if roles is not None else ["requester"],
        existing=existing or [],
        new_id=ids or SequentialIds(),
        at=NOW,
        comment=comment,
        requested_role=role,
        approver_id=approver_id,
    )


def approval(
    role: str, *, hash: str = "h1", version: int = 1, decision: str = "approve"
) -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {
            "id": f"apr_{role}_{decision}_{hash}",
            "session_id": "ses_1",
            "gate": "plan",
            "version": version,
            "hash": hash,
            "approver_id": f"u-{role}",
            "role": role,
            "decision": decision,
        }
    )


# ----------------------------------------------------------------------------- rule 1


def test_rule1_a_gate_shows_one_page_and_two_buttons() -> None:
    brief = make_brief()
    event, status = gates.open_gate(
        gate="brief", version=brief.version, hash=brief.hash, page=gates.brief_page(brief),
        mode="guided", roles=["requester"], approvals=[], session_id="ses_1", event_id="evt_1", at=NOW,
    )  # fmt: skip
    assert isinstance(event.page, BriefPage)
    assert event.buttons == ["Looks right", "Change something"] and not event.single_button
    assert status.status == "open" and event.diff_lines == []


def test_rule1_enterprise_primary_button_is_send_for_approval() -> None:
    assert gates.gate_buttons("enterprise", single_button=False) == [
        "Send for approval",
        "Change something",
    ]
    assert gates.gate_buttons("guided", single_button=False)[0] == "Looks right"


def test_rule1_plan_gate_carries_the_plan_page() -> None:
    spec = make_spec()
    plan = make_plan(spec, spec_hash="x")
    event, _ = gates.open_gate(
        gate="plan", version=1, hash="x", page=gates.plan_page(plan), mode="guided",
        roles=["requester"], approvals=[], session_id="s", event_id="e", at=NOW,
    )  # fmt: skip
    assert isinstance(event.page, PlanPage)


# ----------------------------------------------------------------------------- rule 2


def test_rule2_approval_bound_to_version_and_hash() -> None:
    for version, digest in [(2, "h1"), (1, "h2"), (2, "h2")]:
        with pytest.raises(HashMismatch):
            decide(version=version, hash=digest)


def test_rule2_a_version_change_voids_approvals_and_reopens_with_one_button() -> None:
    old = [approval("requester", hash="h1", version=1)]
    assert (
        gates.derive_status(
            gate="plan", version=1, hash="h1", approvals=old, roles=["requester"]
        ).status
        == "approved"
    )
    new = gates.derive_status(gate="plan", version=2, hash="h2", approvals=old, roles=["requester"])
    assert new.status == "open" and new.approvals == []  # nothing carries over
    event, _ = gates.open_gate(
        gate="plan", version=2, hash="h2", page=gates.plan_page(make_plan(make_spec(version=2), spec_hash="h2")),
        mode="guided", roles=["requester"], approvals=old, session_id="s", event_id="e", at=NOW,
        diff_lines=["Updated the look"],
    )  # fmt: skip
    assert event.single_button and event.buttons == ["Looks right"]
    assert event.approved_roles == [] and event.diff_lines == ["Updated the look"]
    voided = gates.void_gate(new, "version changed")
    assert voided.status == "void" and voided.void_reason == "version changed"


def test_rule2_same_version_number_with_a_different_hash_is_not_the_same_gate() -> None:
    old = [approval("requester", hash="h1", version=1)]
    status = gates.derive_status(
        gate="plan", version=1, hash="h2", approvals=old, roles=["requester"]
    )
    assert status.status == "open"


# ----------------------------------------------------------------------------- rule 3


def _result(**kw: object) -> DiscoveryResult:
    return DiscoveryResult(state=DiscoveryState(), **kw)  # type: ignore[arg-type]


def test_rule3_typed_approval_needs_an_open_gate_and_a_confident_engine() -> None:
    confident = _result(approval_confident=True, intent="approve")
    assert gates.typed_approval_counts(gate_open="plan", result=confident)
    assert not gates.typed_approval_counts(gate_open=None, result=confident)
    assert not gates.typed_approval_counts(
        gate_open="plan", result=_result(approval_confident=False, intent="approve")
    )


def test_rule3_text_that_also_carries_a_change_is_not_an_approval() -> None:
    from mde_contracts.calls import ChangeOp

    mixed = _result(
        approval_confident=True, intent="approve", change_ops=[ChangeOp(kind="set_copy")]
    )
    assert not gates.typed_approval_counts(gate_open="brief", result=mixed)
    assert not gates.typed_approval_counts(
        gate_open="brief", result=_result(approval_confident=True, intent="question")
    )


def test_rule3_the_record_remembers_tap_or_typed() -> None:
    assert decide(via="tap").record.via == "tap"  # type: ignore[union-attr]
    assert decide(via="typed").record.via == "typed"  # type: ignore[union-attr]


# ----------------------------------------------------------------------------- rule 4 / 5 / 6


def test_rule4_a_requested_change_is_recorded_and_needs_no_confirmation() -> None:
    out = decide("change", comment="make it bigger")
    assert out.kind == "change_requested" and out.complete is False
    assert out.comments == ("make it bigger",) and out.record is not None
    assert out.record.decision == "change"


def test_rule5_no_gate_means_no_decision() -> None:
    with pytest.raises(GateError):
        decide(gate_open=False)


def test_rule5_handoff_is_refused_without_an_approval_of_the_current_hash() -> None:
    text = render_spec_text(make_spec())
    digest = sha256_hex(text)
    with pytest.raises(GateError):
        gates.assert_handoff_allowed(
            version=1, spec_hash=digest, spec_text=text, roles=["requester"], approvals=[]
        )
    with pytest.raises(GateError):  # an approval of ANOTHER hash does not count
        gates.assert_handoff_allowed(
            version=1, spec_hash=digest, spec_text=text, roles=["requester"],
            approvals=[approval("requester", hash="other")],
        )  # fmt: skip
    gates.assert_handoff_allowed(
        version=1, spec_hash=digest, spec_text=text, roles=["requester"],
        approvals=[approval("requester", hash=digest)],
    )  # fmt: skip


def test_rule6_silence_never_approves() -> None:
    # no decision exists, however long the gate stays open: the derived status is open
    status = gates.derive_status(
        gate="plan", version=1, hash="h1", approvals=[], roles=["requester"]
    )
    assert status.status == "open"
    # and a gate with no required roles can never be satisfied silently
    assert not gates.is_complete([], [], version=1, hash="h1")


class _Reminders:
    def __init__(self) -> None:
        self.calls: list[str] = []


async def test_rule6_reminders_only_nudge() -> None:
    reminders = gates.NoopReminders()
    await reminders.schedule(
        tenant_id="t", session_id="s", gate="plan", version=1, hash="h", roles=["a"], after_hours=24
    )
    await reminders.cancel(tenant_id="t", session_id="s", gate="plan")
    assert reminders.scheduled[0].after_hours == 24 and reminders.cancelled == [("t", "s", "plan")]


def test_rule6_reminder_hours_come_from_policy() -> None:
    assert gates.reminder_hours(guided_policy(), "plan") is None
    assert gates.reminder_hours(enterprise_policy(reminder_hours=12), "plan") == 12


# ----------------------------------------------------------------------------- rule 7


def test_rule7_required_roles_come_from_policy() -> None:
    assert gates.required_roles(guided_policy(), "plan") == ["requester"]
    ent = enterprise_policy()
    assert gates.required_roles(ent, "brief") == ["requester"]
    assert gates.required_roles(ent, "plan") == ENT_ROLES
    empty = Policy(id="x", gates=PolicyGates(plan=GateRule(approvers=[])))
    assert gates.required_roles(empty, "plan") == ["requester"]  # never an empty gate


def test_rule7_every_required_role_must_approve_the_same_hash() -> None:
    first = decide(who=PO, roles=ENT_ROLES, mode="enterprise")
    assert first.kind == "pending" and not first.complete and first.role == "client_product_owner"
    assert first.record is not None
    second = decide(who=DL, roles=ENT_ROLES, mode="enterprise", existing=[first.record])
    assert second.kind == "approved" and second.complete


def test_rule7_split_approvers_do_not_complete_the_gate() -> None:
    a = decide(who=PO, roles=ENT_ROLES, mode="enterprise")
    assert a.record is not None
    rej = decide("reject", who=DL, roles=ENT_ROLES, mode="enterprise", existing=[a.record],
                 comment="the logo is wrong\nadd a map")  # fmt: skip
    assert rej.kind == "rejected" and not rej.complete
    assert rej.comments == ("the logo is wrong", "add a map")  # each comment is a change request


def test_rule7_approvals_on_different_hashes_do_not_add_up() -> None:
    old = approval("client_product_owner", hash="h0")
    out = decide(who=DL, roles=ENT_ROLES, mode="enterprise", existing=[old])
    assert out.kind == "pending" and not out.complete


def test_rule7_the_latest_decision_per_role_counts() -> None:
    existing = [
        approval("client_product_owner"),
        approval("client_product_owner", decision="reject"),
        approval("delivery_lead"),
    ]
    assert gates.approved_roles(existing, version=1, hash="h1") == ["delivery_lead"]
    assert not gates.is_complete(ENT_ROLES, existing, version=1, hash="h1")
    existing.append(approval("client_product_owner"))
    assert gates.is_complete(ENT_ROLES, existing, version=1, hash="h1")


def test_rule7_duplicate_approval_by_the_same_role_is_a_no_op() -> None:
    first = decide(who=PO, roles=ENT_ROLES, mode="enterprise")
    assert first.record is not None
    again = decide(who=PO, roles=ENT_ROLES, mode="enterprise", existing=[first.record])
    assert again.kind == "duplicate" and again.record is None and not again.complete


def test_rule7_the_requester_submits_but_does_not_approve_for_others() -> None:
    out = decide(who=OWNER, roles=ENT_ROLES, mode="enterprise")
    assert out.kind == "submitted" and out.record is None and not out.complete
    with pytest.raises(GateError):  # in Guided there is no 'submit': a stranger is just refused
        decide(who=Principal(tenant_id="t", user_id="eve", roles=[]), roles=["requester"])


def test_rule7_roles_cannot_be_spoofed() -> None:
    with pytest.raises(GateError):
        decide(who=PO, roles=ENT_ROLES, mode="enterprise", role="delivery_lead")
    with pytest.raises(GateError):
        decide(who=PO, roles=ENT_ROLES, mode="enterprise", approver_id="someone-else")
    with pytest.raises(GateError):
        decide("reject", who=OWNER, roles=ENT_ROLES, mode="enterprise")  # not an approver


def test_rule7_owner_holds_the_requester_role_implicitly() -> None:
    assert gates.held_roles(OWNER, "owner") == {"requester"}
    assert gates.held_roles(PO, "owner") == {"client_product_owner"}


# ----------------------------------------------------------------------------- rule 8


def test_rule8_a_spec_whose_text_does_not_match_its_hash_cannot_be_handed_off() -> None:
    text = render_spec_text(make_spec())
    with pytest.raises(GateError):
        gates.assert_handoff_allowed(
            version=1, spec_hash="0" * 64, spec_text=text, roles=["requester"],
            approvals=[approval("requester", hash="0" * 64)],
        )  # fmt: skip


def test_rule8_the_requesters_light_approval_completes_only_a_requester_only_gate() -> None:
    """A light post-handoff change is covered by the requester's own recorded approval, which is
    honest only when the requester IS the whole gate. It never stands in for other approvers."""
    text = render_spec_text(make_spec(version=2))
    digest = sha256_hex(text)
    marked = ApprovalRecord.model_validate(
        {
            "id": "apr_x", "session_id": "s", "gate": "plan", "version": 2, "hash": digest,
            "approver_id": "owner", "role": "requester", "decision": "approve",
            "comment": gates.LIGHT_CHANGE_COMMENT,
        }
    )  # fmt: skip
    gates.assert_handoff_allowed(
        version=2, spec_hash=digest, spec_text=text, roles=["requester"], approvals=[marked]
    )
    for roles in (ENT_ROLES, ["client_product_owner"], ["requester", "delivery_lead"]):
        with pytest.raises(GateError):  # the marked record is no way round the other approvers
            gates.assert_handoff_allowed(
                version=2, spec_hash=digest, spec_text=text, roles=roles, approvals=[marked]
            )
    with pytest.raises(GateError):  # and no record at all is refused
        gates.assert_handoff_allowed(
            version=2, spec_hash=digest, spec_text=text, roles=["requester"], approvals=[]
        )


def test_split_comments_ignores_blank_lines() -> None:
    assert gates.split_comments("a\n\n  b  \n") == ("a", "b")
    assert gates.split_comments(None) == () and gates.split_comments("") == ()


def test_plan_enterprise_extras_only_for_enterprise_or_multi_approver() -> None:
    plan = make_plan(make_spec(), spec_hash="h1")
    guided = gates.plan_with_enterprise_extras(
        plan, mode="guided", policy=guided_policy(), approvals=[], roles=["requester"]
    )
    assert guided.enterprise is None
    ent = enterprise_policy()
    out = gates.plan_with_enterprise_extras(
        plan, mode="enterprise", policy=ent, approvals=[approval("delivery_lead", decision="reject")],
        roles=ENT_ROLES,
    )  # fmt: skip
    assert out.enterprise is not None and out.enterprise.full_file_view
    assert [(a.role, a.decision) for a in out.enterprise.approvers] == [
        ("client_product_owner", None),
        ("delivery_lead", "reject"),
    ]
