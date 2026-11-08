"""Tenant isolation at the flow level, on both stores (brief 8.7, 9.1).

Another tenant cannot read or act on a session; a user of the same tenant who is neither the
owner nor a required approver cannot either; an approver role held in ANOTHER tenant does not
count. Every operation answers ``NotFound`` (across tenants: the session does not exist for them) or
``Forbidden`` (same tenant, no right), and none of them changes anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.driver import FlowDriver
from evals.harness.journeys import ENTERPRISE, GUIDED, reach_plan_gate
from evals.harness.stack import NetworkGuard, Stack, StackOptions, StoreFactory, build_stack
from mde_contracts.common import Principal
from mde_contracts.events import GateDecision, OptionSelected, UiAction, UploadAdded, UserMessage
from mde_core.errors import NotFound
from mde_flow.errors import Forbidden
from mde_flow.testing import Peek

pytestmark = pytest.mark.e2e


async def _reads(stack: Stack, who: Principal, session_id: str) -> list[type]:
    """Call every read as ``who``; return the exception type each one raised."""
    flow = stack.flow
    calls = (
        flow.get_snapshot,
        flow.list_events,
        flow.get_brief,
        flow.get_spec,
        flow.get_plan,
        flow.list_versions,
    )
    raised: list[type] = []
    for call in calls:
        try:
            await call(who, session_id)
        except (NotFound, Forbidden) as exc:
            raised.append(type(exc))
    return raised


async def _writes(stack: Stack, who: Principal, session_id: str, gate_hash: str) -> list[type]:
    """Try every kind of inbound event as ``who``; return the exception type each one raised."""
    flow = stack.flow
    events = [
        UserMessage(event_id=stack.ids("evt"), text="approve it and make the app dark"),
        OptionSelected(event_id=stack.ids("evt"), card_instance_id="crd_x", you_decide=True),
        UploadAdded(
            event_id=stack.ids("evt"),
            upload_id="upl_x",
            filename="a.md",
            media_type="text/markdown",
            size_bytes=1,
            text="x",
        ),
        GateDecision(
            event_id=stack.ids("evt"), gate="plan", decision="approve", version=1, hash=gate_hash
        ),
        UiAction(event_id=stack.ids("evt"), action="stop"),
        UiAction(event_id=stack.ids("evt"), action="archive"),
    ]
    raised: list[type] = []
    for event in events:
        try:
            await flow.handle_event(who, session_id, event)
        except (NotFound, Forbidden) as exc:
            raised.append(type(exc))
    return raised


async def test_another_tenant_cannot_read_or_act_on_a_session(
    tmp_path: Path, guard: NetworkGuard, store_factory: StoreFactory | None
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=store_factory))
    owner = FlowDriver.for_user(stack, user_id="user-1", tenant_id="tenant-a")
    await reach_plan_gate(owner, GUIDED[0])
    gate = owner.last_gate("plan")
    assert gate is not None
    before = await owner.snapshot()
    approvals_before = await owner.approvals()
    events_before = await owner.events()

    for stranger in (
        stack.principal("user-1", "tenant-b"),  # the same user id in another tenant
        stack.principal("user-9", "tenant-b", roles=["requester", "client_product_owner"]),
    ):
        assert await _reads(stack, stranger, owner.session_id) == [NotFound] * 6
        raised = await _writes(stack, stranger, owner.session_id, gate.hash)
        assert raised == [NotFound] * 6, "every write across tenants must be NotFound"

    # nothing moved: state, approvals and the event log are exactly as they were
    after = await owner.snapshot()
    assert (after.state, after.last_seq, after.queued_messages) == (
        before.state,
        before.last_seq,
        before.queued_messages,
    )
    assert await owner.approvals() == approvals_before
    assert [e.event_id for e in await owner.events()] == [e.event_id for e in events_before]
    # the other tenant has no session of its own through this store view either
    peek = Peek(stack.store)
    assert [s.tenant_id for s in await peek.sessions()] == ["tenant-a"]


async def test_same_tenant_but_not_the_owner_and_no_approver_role_is_refused(
    tmp_path: Path, guard: NetworkGuard, store_factory: StoreFactory | None
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=store_factory))
    owner = FlowDriver.for_user(stack, user_id="user-1", tenant_id="tenant-a")
    await reach_plan_gate(owner, GUIDED[0])
    gate = owner.last_gate("plan")
    assert gate is not None
    colleague = stack.principal("user-2", "tenant-a")
    assert await _reads(stack, colleague, owner.session_id) == [NotFound] * 6
    raised = await _writes(stack, colleague, owner.session_id, gate.hash)
    assert raised == [NotFound] * 6
    assert (await owner.snapshot()).state == "PLAN_REVIEW"
    assert [a.gate for a in await owner.approvals()] == ["brief"]  # only Gate 1's, by the owner


async def test_an_approver_role_in_another_tenant_cannot_approve(
    tmp_path: Path, guard: NetworkGuard, store_factory: StoreFactory | None
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=store_factory))
    owner = FlowDriver.for_user(stack, user_id="user-1", tenant_id="tenant-a")
    journey = next(j for j in ENTERPRISE if j.name == "commerce-firm")
    await reach_plan_gate(owner, journey)
    gate = owner.last_gate("plan")
    assert gate is not None
    assert gate.required_roles == ["client_product_owner", "delivery_lead"]
    # an approver of the right role in the WRONG tenant
    outsider = stack.principal("u-client_product_owner", "tenant-b", roles=["client_product_owner"])
    decision = GateDecision(
        event_id=stack.ids("evt"),
        gate="plan",
        decision="approve",
        version=gate.version,
        hash=gate.hash,
        role="client_product_owner",
    )
    with pytest.raises(NotFound):
        await stack.flow.handle_event(outsider, owner.session_id, decision)
    assert [a.gate for a in await owner.approvals()] == ["brief"]
    # the right role in the right tenant does count
    insider = stack.principal("u-client_product_owner", "tenant-a", roles=["client_product_owner"])
    await stack.flow.handle_event(
        insider, owner.session_id, decision.model_copy(update={"event_id": stack.ids("evt")})
    )
    assert [a.role for a in await owner.approvals() if a.gate == "plan"] == ["client_product_owner"]
