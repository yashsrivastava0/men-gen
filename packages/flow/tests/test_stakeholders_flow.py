"""GAP-3 through the flow: two Enterprise stakeholders answer the same slot differently.

Spec 9: "record both; resolve by the role precedence in policy or flag to the decision owner; never
overwrite silently". The approver's answer arrives as the comment of a "change" decision. Run over
both stores."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from mde_contracts.common import Principal
from mde_contracts.events import Notice, OutboundEvent
from mde_flow import bookkeeping, messages, stakeholders
from mde_flow.testing import enterprise_policy
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit, principal

LOOK = "design.direction"
PAT = principal("u-pat", roles=["product_owner"])
PAM = principal("u-pam", roles=["product_owner"])


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


def kit_with(precedence: Sequence[str]) -> FlowKit:
    policy = enterprise_policy(
        policy_id="enterprise-default",
        brief_approvers=["requester", "product_owner"],
        role_precedence=precedence,
    )
    return make_flow_kit(policies=[policy])


def codes(events: Sequence[OutboundEvent]) -> list[str]:
    return [e.code for e in events if isinstance(e, Notice)]


async def at_gate1_with_bold_dark(kit: FlowKit) -> Driver:
    """The requester picked a look at Gate 1, then changed it to bold-dark (version 2)."""
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.say(f"set {LOOK}=bold-dark")
    assert d.last_gate("brief") is not None and d.last_gate("brief").version == 2  # type: ignore[union-attr]
    return d


async def comment(d: Driver, who: Principal, value: str, role: str = "product_owner"):  # type: ignore[no-untyped-def]
    return await d.decide("brief", "change", comment=f"set {LOOK}={value}", who=who, role=role)


async def slot(kit: FlowKit, d: Driver, slot_id: str = LOOK):  # type: ignore[no-untyped-def]
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    return rec.discovery.slots.get(slot_id), rec


async def test_the_higher_role_wins_and_both_answers_are_on_record() -> None:
    kit = kit_with(["product_owner", "requester"])
    d = await at_gate1_with_bold_dark(kit)
    out = await comment(d, PAT, "warm-friendly")
    assert codes(out) == [messages.N_SLOT_CONFLICT]
    text = next(e.text for e in out if isinstance(e, Notice))
    assert "bold-dark from requester" in text and "warm-friendly from product_owner" in text
    assert "The policy puts product_owner first" in text
    rec_slot, rec = await slot(kit, d)
    assert (
        rec_slot is not None and rec_slot.value == "warm-friendly" and rec_slot.state != "disputed"
    )
    book = stakeholders.load_conflicts(rec.discovery.extra[bookkeeping.SLOT_CONFLICTS])
    assert [(s.user, s.roles, s.value) for s in book[LOOK].stakes] == [
        ("user-1", ["requester"], "bold-dark"),
        ("u-pat", ["product_owner"], "warm-friendly"),
    ]
    assert book[LOOK].status == "resolved" and book[LOOK].how == "precedence"
    assert d.last_gate("brief").version == 3  # type: ignore[union-attr]  # the change went through


async def test_a_lower_role_cannot_overwrite_and_the_gate_stays_as_it_was() -> None:
    kit = kit_with(["requester", "product_owner"])
    d = await at_gate1_with_bold_dark(kit)
    out = await comment(d, PAT, "warm-friendly")
    assert codes(out) == [messages.N_SLOT_CONFLICT]
    assert not any(getattr(e, "text", "").startswith("Done") for e in out)  # no "I updated" chat
    rec_slot, rec = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "bold-dark"
    book = stakeholders.load_conflicts(rec.discovery.extra[bookkeeping.SLOT_CONFLICTS])
    assert [s.value for s in book[LOOK].stakes] == ["bold-dark", "warm-friendly"]  # both kept
    assert rec.state == "BRIEF_REVIEW" and rec.brief_version == 2  # nothing changed: same Brief
    assert messages.N_NOT_UNDERSTOOD not in codes(out)  # the disagreement is the answer


async def test_when_precedence_cannot_decide_the_owner_is_asked_and_approval_waits() -> None:
    kit = kit_with([])
    d = await at_gate1_with_bold_dark(kit)
    out = await comment(d, PAT, "warm-friendly")
    assert codes(out) == [messages.N_SLOT_DISPUTED]
    text = next(e.text for e in out if isinstance(e, Notice))
    assert "The person who owns this app needs to choose" in text
    rec_slot, _ = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "bold-dark" and rec_slot.state == "disputed"
    snap = await d.snapshot()
    assert snap.slots[LOOK].state == "disputed"

    # everyone says yes, but the Brief is still in dispute: the gate does not complete
    await d.decide("brief", "approve", who=PAT, role="product_owner")
    out = await d.decide("brief", "approve")
    assert messages.N_DISPUTE_BLOCKS in codes(out)
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert await kit.peek.jobs(d.session_id) == []

    # the owner chooses: the dispute is settled and the Brief changes (approvals void)
    out = await d.say(f"set {LOOK}=warm-friendly")
    assert messages.N_SLOT_SETTLED in codes(out)
    rec_slot, rec = await slot(kit, d)
    assert (
        rec_slot is not None and rec_slot.value == "warm-friendly" and rec_slot.state == "confirmed"
    )
    assert (
        stakeholders.open_disputes(
            stakeholders.load_conflicts(rec.discovery.extra[bookkeeping.SLOT_CONFLICTS])
        )
        == []
    )
    assert d.last_gate("brief").approved_roles == []  # type: ignore[union-attr]
    await d.decide("brief", "approve", who=PAT, role="product_owner")
    await d.decide("brief", "approve")
    assert (await d.snapshot()).state == "PLANNING"  # and now the run starts


async def test_someone_else_cannot_settle_a_dispute_and_their_answer_is_noted() -> None:
    kit = kit_with([])
    d = await at_gate1_with_bold_dark(kit)
    await comment(d, PAT, "warm-friendly")
    out = await comment(d, PAM, "clean-bright")
    assert codes(out) == [messages.N_SLOT_DISPUTED]
    rec_slot, rec = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "bold-dark" and rec_slot.state == "disputed"
    book = stakeholders.load_conflicts(rec.discovery.extra[bookkeeping.SLOT_CONFLICTS])
    assert [s.value for s in book[LOOK].stakes] == ["bold-dark", "warm-friendly", "clean-bright"]


async def test_a_first_answer_from_an_approver_is_not_a_disagreement() -> None:
    kit = kit_with([])
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    out = await d.decide(
        "brief", "change", comment="set pay.model=card", who=PAT, role="product_owner"
    )
    assert messages.N_SLOT_DISPUTED not in codes(out) and messages.N_SLOT_CONFLICT not in codes(out)
    rec_slot, _ = await slot(kit, d, "pay.model")
    assert rec_slot is not None and rec_slot.value == "card"


async def test_the_requester_changing_their_own_mind_is_never_a_disagreement() -> None:
    kit = kit_with([])
    d = await at_gate1_with_bold_dark(kit)
    out = await d.say(f"set {LOOK}=warm-friendly")
    assert messages.N_SLOT_DISPUTED not in codes(out) and messages.N_SLOT_CONFLICT not in codes(out)
    rec_slot, _ = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "warm-friendly"


async def test_the_requester_overruling_an_approver_follows_the_policy_too() -> None:
    kit = kit_with(["product_owner", "requester"])
    d = await at_gate1_with_bold_dark(kit)
    await comment(d, PAT, "warm-friendly")  # the product owner outranks the requester: it stands
    out = await d.say(f"set {LOOK}=clean-bright")  # the requester disagrees: precedence says no
    assert codes(out) == [messages.N_SLOT_CONFLICT]
    rec_slot, _ = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "warm-friendly"


async def test_guided_sessions_keep_no_stakeholder_bookkeeping() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    _, rec = await slot(kit, d)
    assert bookkeeping.SLOT_ACTORS not in rec.discovery.extra
    assert bookkeeping.SLOT_CONFLICTS not in rec.discovery.extra


async def test_a_note_typed_during_planning_is_settled_as_the_requesters() -> None:
    """The queue is replayed when the plan is ready, by the job: it speaks for the owner."""
    kit = kit_with(["product_owner", "requester"])
    d = await at_gate1_with_bold_dark(kit)
    await comment(d, PAT, "warm-friendly")  # the product owner outranks the requester
    await d.decide("brief", "approve", who=PAT, role="product_owner")
    await d.decide("brief", "approve")
    assert (await d.snapshot()).state == "PLANNING"
    hold = kit.research.pause_inside()  # the run is genuinely in progress, so a note is queued
    run = asyncio.create_task(kit.service.run_next_job())
    await asyncio.wait_for(hold.entered.wait(), timeout=10)
    await d.say(f"set {LOOK}=clean-bright")
    assert (await d.snapshot()).queued_messages == 1
    hold.release()
    await asyncio.wait_for(run, timeout=30)
    await d.refresh()
    notes = [e for e in d.events if isinstance(e, Notice) and e.code == messages.N_SLOT_CONFLICT]
    assert notes and "The policy puts product_owner first" in notes[-1].text
    rec_slot, _ = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "warm-friendly"  # the requester lost again


def test_notice_helpers_say_slots_and_values_in_words() -> None:
    from mde_flow import service

    assert service._slot_name("design.direction") == "Design direction"
    assert service._slot_name("scope.features") == "Scope features"
    assert service._show(["browse", "chat"]) == "browse, chat"
    assert service._show(None) == "nothing" and service._show(False) == "False"
    assert service._roles_text(["requester", "product_owner"]) == "product_owner and requester"
    assert service._roles_text([]) == "someone"
    assert service._same_words("Use the default!", "use  the DEFAULT") is True
    assert service._same_words("use the defaults", "use the default") is False


def test_a_set_slot_op_names_its_value_as_a_list_a_flag_or_text() -> None:
    from mde_contracts.calls import ChangeOp
    from mde_flow import service

    base: dict[str, object] = {"kind": "set_slot", "slot_id": "scope.features"}
    assert service._op_value(ChangeOp.model_validate({**base, "value_list": ["a"]})) == ["a"]
    assert service._op_value(ChangeOp.model_validate({**base, "value_bool": False})) is False
    assert service._op_value(ChangeOp.model_validate({**base, "value_text": " x "})) == "x"
    assert service._op_value(ChangeOp.model_validate(base)) is None


async def test_an_approver_is_counted_under_the_role_they_decided_as() -> None:
    """Someone who holds two roles speaks for the one they pressed the button as."""
    kit = kit_with(["compliance_officer", "requester", "product_owner"])
    d = await at_gate1_with_bold_dark(kit)
    both = principal("u-pat", roles=["product_owner", "compliance_officer"])
    out = await comment(d, both, "warm-friendly", role="product_owner")
    # as a product owner they rank below the requester: the requester's answer stands
    assert codes(out) == [messages.N_SLOT_CONFLICT]
    assert "The policy puts requester first" in next(e.text for e in out if isinstance(e, Notice))
    rec_slot, _ = await slot(kit, d)
    assert rec_slot is not None and rec_slot.value == "bold-dark"
