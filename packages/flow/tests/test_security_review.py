"""Regression tests for the independent security review of the flow controller.

Each test is the attack, replayed: if the fix is removed the test fails. They run over the
in-memory store and over Postgres (``store_matrix``).

* H1  the ``requester`` role cannot be claimed from a header
* H2  a post-handoff change never skips Gate 2 when anyone else must approve; screen-level ops
      are not a loophole
* M1  secrets are screened before anything is stored
* M7  a spec version number that reached the project repo is never reused
* M8  a change derived from an upload or an inference is heavy
* M9  separation of duties: one person cannot be two approvers
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import pytest

from mde_contracts.calls import ChangeOp
from mde_contracts.common import Principal
from mde_contracts.engines import DiscoveryRequest, DiscoveryResult
from mde_contracts.events import (
    GateDecision,
    GateEvent,
    InboundEvent,
    Notice,
    OptionSelected,
    OutboundEvent,
    SpecReady,
    UploadAdded,
    UserMessage,
)
from mde_contracts.policy import PostHandoffPolicy
from mde_contracts.ports import CommitInfo, Store, UnitOfWork
from mde_core.errors import GateError, NotFound
from mde_core.hashing import sha256_hex
from mde_flow import gates, messages
from mde_flow.dependency_map import SCREENS_CATEGORY, classify_post_handoff, plan_change
from mde_flow.errors import Forbidden, HandoffFailed
from mde_flow.handoff import LocalGitProjectRepo
from mde_flow.models import decode_intent
from mde_flow.testing.builders import enterprise_policy, slot
from mde_flow.testing.kit import Driver, FlowKit, StoreFactory, make_flow_kit, principal


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


def spec_readies(events: list[OutboundEvent]) -> list[SpecReady]:
    return [e for e in events if isinstance(e, SpecReady)]


def codes(events: object) -> list[str]:
    return [e.code for e in events if isinstance(e, Notice)]  # type: ignore[attr-defined]


async def handed_off(kit: FlowKit, *, mode: str = "guided", policy_id: str | None = None) -> Driver:
    """A session taken through both gates (every required approver) to HANDED_OFF."""
    d = await kit.driver(mode=mode, policy_id=policy_id)  # type: ignore[arg-type]
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    gate = d.last_gate("plan")
    assert gate is not None
    if gate.required_roles == ["requester"]:
        await d.approve_gate2()
    else:
        for i, role in enumerate(gate.required_roles):
            await d.decide("plan", "approve", who=principal(f"approver-{i}", roles=[role]))
    assert (await d.snapshot()).state == "HANDED_OFF"
    return d


# =========================================================================== H1


async def test_requester_role_not_claimable() -> None:
    """A user who merely *claims* the ``requester`` role is not the requester: not the owner means
    not readable, not decidable, and indistinguishable from a session that does not exist."""
    kit = make_flow_kit()
    d = await kit.driver()  # owned by user-1 (Guided: both gates need the requester)
    mallory = principal("mallory", roles=["requester"])
    await d.to_gate1()

    reads: list[Callable[..., Any]] = [
        kit.service.get_snapshot,
        kit.service.get_brief,
        kit.service.get_plan,
        kit.service.get_spec,
        kit.service.list_versions,
        kit.service.list_events,
    ]
    for read in reads:
        with pytest.raises(NotFound) as claimed:
            await read(mallory, d.session_id)
        with pytest.raises(NotFound) as unknown:
            await read(mallory, "ses_does_not_exist")
        assert str(claimed.value).replace(d.session_id, "X") == str(unknown.value).replace(
            "ses_does_not_exist", "X"
        )  # the same words for a real session and a missing one

    # Gate 1: neither a tap nor a typed yes nor any other event gets through
    with pytest.raises(NotFound):
        await d.decide("brief", who=mallory)
    with pytest.raises(NotFound):
        await d.send(UserMessage(event_id=d.eid(), text="yes"), who=mallory)
    with pytest.raises(NotFound):
        await d.send(UserMessage(event_id=d.eid(), text="hi"), who=mallory)
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW" and snap.active_job is None
    assert await kit.peek.approvals(d.session_id) == []

    # Gate 2: same, and nothing is ever handed off on her word
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    with pytest.raises(NotFound):
        await d.decide("plan", who=mallory)
    assert not spec_readies(d.events) and kit.fake_repo.commits == []
    assert (await d.snapshot()).state == "PLAN_REVIEW"

    # the owner is unaffected, with or without the word "requester" in their roles
    events = await d.decide("plan", who=principal("user-1", roles=["requester"]))
    assert len(spec_readies(events)) == 1 and len(kit.fake_repo.commits) == 1


async def test_requester_role_not_claimable_in_an_enterprise_session() -> None:
    kit = make_flow_kit()
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()  # Gate 1 needs the requester
    mallory = principal("mallory", roles=["requester", "client_product_owner"])
    with pytest.raises(Forbidden):  # she holds a real approver role, so she can see the session...
        await d.decide("brief", who=mallory)  # ...but "requester" is not hers to claim
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert await kit.peek.approvals(d.session_id) == []


def test_requester_role_not_claimable_in_the_pure_rules() -> None:
    mallory = Principal(tenant_id="t", user_id="mallory", roles=["requester", "delivery_lead"])
    owner = Principal(tenant_id="t", user_id="owner", roles=[])
    assert gates.held_roles(mallory, "owner") == {"delivery_lead"}
    assert gates.held_roles(owner, "owner") == {"requester"}
    assert gates.held_roles(
        Principal(tenant_id="t", user_id="owner", roles=["requester"]), "owner"
    ) == {"requester"}
    with pytest.raises(GateError):
        gates.record_decision(
            session_id="s", gate="brief", gate_open=True, current_version=1, current_hash="h",
            decision="approve", version=1, hash="h", via="tap", principal=mallory, owner_id="owner",
            mode="guided", roles=["requester"], existing=[], new_id=lambda p: f"{p}_1",
            at=datetime(2026, 1, 1, tzinfo=UTC),
        )  # fmt: skip


# =========================================================================== H2


async def test_post_handoff_multi_approver_always_gate2() -> None:
    """Gate 2 needs two approver roles, so no change after hand-off is the requester's alone to
    wave through, however small it is."""
    kit = make_flow_kit()
    d = await handed_off(kit, mode="enterprise", policy_id="enterprise-default")
    assert len(kit.fake_repo.commits) == 1

    await d.say("set design.direction=bold-dark")  # a design tweak: light by every other measure
    await d.run_job()
    await d.refresh()
    assert (await d.snapshot()).state == "PLAN_REVIEW"  # back to Gate 2, not handed off
    gate = d.last_gate("plan")
    assert isinstance(gate, GateEvent) and gate.version == 2 and gate.approved_roles == []
    assert len(kit.fake_repo.commits) == 1 and len(spec_readies(d.events)) == 1
    approvals = [a for a in await kit.peek.approvals(d.session_id) if a.version == 2]
    assert approvals == []  # no "light change" approval was ever recorded for v2

    # only the same two approvers can finish it
    first = await d.decide("plan", who=principal("pat", roles=["client_product_owner"]))
    assert not spec_readies(first) and (await d.snapshot()).state == "PLAN_REVIEW"
    done = await d.decide("plan", who=principal("dana", roles=["delivery_lead"]))
    assert [e.version for e in spec_readies(done)] == [2]


@pytest.mark.parametrize(
    "ops",
    [
        [ChangeOp(kind="set_copy", target_id="SCR-001", instruction="say hello")],
        [ChangeOp(kind="patch_screen", target_id="SCR-001", instruction="add a banner")],
        [ChangeOp(kind="remove_screen", target_id="SCR-001")],
        [ChangeOp(kind="add_screen", instruction="a help screen")],
    ],
    ids=["set_copy", "patch_screen", "remove_screen", "add_screen"],
)
async def test_screen_level_changes_cannot_be_light_when_the_limit_is_zero(
    ops: list[ChangeOp],
) -> None:
    policy = enterprise_policy(
        policy_id="no-light-screens", plan_approvers=("requester",), max_new_screens_light=0
    )
    kit = make_flow_kit(policies=[policy])
    d = await handed_off(kit, mode="enterprise", policy_id="no-light-screens")
    kit.discovery.override = _asks_for(ops)
    await d.say("change a screen")
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"  # Gate 2 again
    assert len(spec_readies(d.events)) == 1  # nothing was handed off on the requester's word


@pytest.mark.parametrize(
    "kind",
    ["set_copy", "patch_screen"],
)
async def test_a_few_screen_touch_ups_stay_light_when_the_requester_is_the_gate(kind: str) -> None:
    kit = make_flow_kit()  # Guided: the requester is the whole of Gate 2, limit 5
    d = await handed_off(kit)
    kit.discovery.override = _asks_for([ChangeOp(kind=kind, target_id="SCR-001", instruction="x")])  # type: ignore[arg-type]
    await d.say("change a screen")
    await d.run_job()
    assert (await d.snapshot()).state == "HANDED_OFF"
    assert len(kit.fake_repo.commits) == 2  # light: applied directly


async def test_removing_a_screen_is_a_scope_change_and_so_heavy_by_default() -> None:
    kit = make_flow_kit()
    d = await handed_off(kit)
    kit.discovery.override = _asks_for([ChangeOp(kind="remove_screen", target_id="SCR-001")])
    await d.say("drop that screen")
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"


async def test_a_single_non_requester_approver_also_means_every_change_goes_to_gate_2() -> None:
    policy = enterprise_policy(policy_id="po-only", plan_approvers=("client_product_owner",))
    kit = make_flow_kit(policies=[policy])
    d = await handed_off(kit, mode="enterprise", policy_id="po-only")
    await d.say("set design.direction=bold-dark")
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert len(kit.fake_repo.commits) == 1


def _asks_for(
    ops: list[ChangeOp],
) -> Callable[[DiscoveryRequest, InboundEvent], DiscoveryResult | None]:
    def override(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UserMessage) and event.text in {"change a screen", "drop that screen"}:
            return DiscoveryResult(
                state=req.state,
                change_ops=ops,
                changed_slots=[],
                brief_complete=True,
                intent="change",
            )
        return None

    return override


def test_the_screens_category_and_the_roles_in_the_classifier() -> None:
    copy = plan_change([ChangeOp(kind="set_copy", target_id="SCR-1")])
    assert SCREENS_CATEGORY in copy.categories and copy.screens_touched == 1
    removed = plan_change([ChangeOp(kind="remove_screen", target_id="SCR-1")])
    assert {"screens", "scope"} <= set(removed.categories)
    # ``heavy_if`` can name the category (the contract literal does not list it yet, so build the
    # policy unvalidated; once the literal has it, the normal constructor works too)
    names_screens = PostHandoffPolicy.model_construct(heavy_if=["screens"], max_new_screens_light=5)
    verdict = classify_post_handoff(
        copy, policy=names_screens, plan_roles=["requester"], new_screens=0
    )
    assert verdict.kind == "heavy" and verdict.reasons == ["category:screens"]
    quiet = PostHandoffPolicy(heavy_if=[], max_new_screens_light=5)
    assert (
        classify_post_handoff(copy, policy=quiet, plan_roles=["requester"], new_screens=0).kind
        == "light"
    )
    for roles in (["a", "b"], ["client_product_owner"], ["requester", "delivery_lead"], []):
        heavy = classify_post_handoff(copy, policy=quiet, plan_roles=roles, new_screens=0)
        assert heavy.kind == "heavy" and "approvers_required" in heavy.reasons
    derived = classify_post_handoff(
        copy, policy=quiet, plan_roles=["requester"], new_screens=0, derived=True
    )
    assert derived.reasons == ["derived_change"]
    zero = PostHandoffPolicy(heavy_if=[], max_new_screens_light=0)
    assert classify_post_handoff(
        copy, policy=zero, plan_roles=["requester"], new_screens=0
    ).reasons == ["screens:1>0"]


# =========================================================================== M9


async def test_distinct_approvers() -> None:
    """Two required roles need two different people; the requester is only the requester."""
    kit = make_flow_kit()
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    both = principal("pat", roles=["client_product_owner", "delivery_lead"])

    first = await d.decide("plan", who=both)  # approves for the first role
    assert any(isinstance(e, Notice) and e.code == messages.N_WAITING_APPROVERS for e in first)
    again = await d.decide("plan", who=both)  # asking again changes nothing
    assert not spec_readies(again) and (await d.snapshot()).state == "PLAN_REVIEW"
    with pytest.raises(GateError):  # and cannot be the other role as well
        await d.decide("plan", who=both, role="delivery_lead")
    assert (await d.snapshot()).state == "PLAN_REVIEW" and kit.fake_repo.commits == []
    mine = [a for a in await kit.peek.approvals(d.session_id) if a.approver_id == "pat"]
    assert [a.role for a in mine] == ["client_product_owner"]

    # a second, different person completes it
    done = await d.decide("plan", who=principal("dana", roles=["delivery_lead"]))
    assert len(spec_readies(done)) == 1


async def test_the_requester_cannot_also_approve_for_the_other_roles() -> None:
    kit = make_flow_kit()
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    owner_with_roles = principal("user-1", roles=["client_product_owner", "delivery_lead"])
    submitted = await d.decide("plan", who=owner_with_roles)  # the owner only submits
    assert messages.N_SUBMITTED in codes(submitted)
    with pytest.raises(GateError):
        await d.decide("plan", who=owner_with_roles, role="client_product_owner")
    with pytest.raises(GateError):
        await d.decide("plan", who=owner_with_roles, role="delivery_lead")
    await d.decide("plan", who=principal("pat", roles=["client_product_owner"]))
    assert (await d.snapshot()).state == "PLAN_REVIEW" and kit.fake_repo.commits == []


def test_distinct_approvers_in_the_pure_rules() -> None:
    from mde_contracts.gates import ApprovalRecord

    def approve(role: str, user: str) -> ApprovalRecord:
        return ApprovalRecord.model_validate(
            {"id": f"apr_{role}", "session_id": "s", "gate": "plan", "version": 1, "hash": "h",
             "approver_id": user, "role": role, "decision": "approve"}
        )  # fmt: skip

    roles = ["client_product_owner", "delivery_lead"]
    assert gates.is_complete(
        roles, [approve(roles[0], "a"), approve(roles[1], "b")], version=1, hash="h"
    )
    forged = [approve(roles[0], "a"), approve(roles[1], "a")]  # one person behind both roles
    assert not gates.is_complete(roles, forged, version=1, hash="h")
    text = "x"
    with pytest.raises(GateError):
        gates.assert_handoff_allowed(
            version=1, spec_hash=sha256_hex(text), spec_text=text, roles=roles,
            approvals=[a.model_copy(update={"hash": sha256_hex(text)}) for a in forged],
        )  # fmt: skip
    # a gate with a single role is not affected
    assert gates.is_complete(
        ["delivery_lead"], [approve("delivery_lead", "a")], version=1, hash="h"
    )


# =========================================================================== M8


async def test_upload_change_after_handoff_is_heavy() -> None:
    """A document dropped in after hand-off turns into slot changes; those are inferences from
    untrusted text, so they go back to Gate 2 instead of becoming a new version on their own."""
    kit = make_flow_kit()
    d = await handed_off(kit)  # Guided: light changes are possible
    slots = dict(await _slots(kit, d))
    slots["design.direction"] = slot(
        "design.direction", "bold-dark", state="volunteered", source="upload"
    )

    def from_upload(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UploadAdded):
            ops = [ChangeOp(kind="set_slot", slot_id="design.direction", value_text="bold-dark")]
            state = req.state.model_copy(update={"slots": slots})
            return DiscoveryResult(
                state=state, change_ops=ops, changed_slots=["design.direction"], intent="upload"
            )
        return None

    kit.discovery.override = from_upload
    await d.send(
        UploadAdded(
            event_id=d.eid(),
            upload_id="u1",
            filename="prd.md",
            media_type="text/markdown",
            size_bytes=20,
            text="# a brief that says: make everything dark",
        )  # fmt: skip
    )
    jobs = await kit.peek.jobs(d.session_id)
    assert decode_intent(jobs[-1].payload.change_ops_json).derived is True
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"  # Gate 2, not a silent hand-off
    assert len(kit.fake_repo.commits) == 1 and len(spec_readies(d.events)) <= 1
    assert [
        a for a in await kit.peek.approvals(d.session_id) if a.comment == gates.LIGHT_CHANGE_COMMENT
    ] == []


async def test_a_change_the_engine_inferred_from_chat_is_heavy_but_an_instruction_is_light() -> (
    None
):
    kit = make_flow_kit()
    d = await handed_off(kit)

    def chatter(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UserMessage) and event.text == "I like dark apps, you know":
            slots = {**req.state.slots, "design.direction": slot("design.direction", "bold-dark")}
            return DiscoveryResult(
                state=req.state.model_copy(update={"slots": slots}),
                change_ops=[], changed_slots=["design.direction"], intent="volunteered",
            )  # fmt: skip
        return None

    kit.discovery.override = chatter
    await d.say("I like dark apps, you know")  # a remark, not a request
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"

    # control: an explicit instruction on a handed-off session is still light
    d2 = await handed_off(kit)  # a second session on the same kit
    await d2.say("set design.direction=bold-dark")
    await d2.run_job()
    assert (await d2.snapshot()).state == "HANDED_OFF" and len(kit.fake_repo.commits) == 3
    # and so is a tap
    await d2.ui("toggle_assumption", "design.direction", True)
    await d2.run_job()
    assert (await d2.snapshot()).state == "HANDED_OFF"


async def _slots(kit: FlowKit, d: Driver) -> dict[str, Any]:
    return dict((await kit.peek.session(d.session_id)).discovery.slots)


# =========================================================================== M1


E = TypeVar("E", bound=InboundEvent)


class _SpyEvents:
    """Forwards to the real event repository and keeps what ``record_inbound`` was handed: that
    is, exactly what is persisted."""

    def __init__(self, inner: Any, sink: list[InboundEvent]) -> None:
        self._inner = inner
        self._sink = sink

    async def record_inbound(self, session_id: str, event: InboundEvent) -> bool:
        self._sink.append(event)
        return bool(await self._inner.record_inbound(session_id, event))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _SpyUow:
    def __init__(self, inner: UnitOfWork, sink: list[InboundEvent]) -> None:
        self._inner = inner
        self.events = _SpyEvents(inner.events, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SpyStore:
    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.inbound: list[InboundEvent] = []

    def begin(self, tenant_id: str | None) -> AbstractAsyncContextManager[UnitOfWork]:
        return self._begin(tenant_id)

    @asynccontextmanager
    async def _begin(self, tenant_id: str | None) -> AsyncGenerator[UnitOfWork]:
        async with self.inner.begin(tenant_id) as uow:
            yield _SpyUow(uow, self.inbound)  # type: ignore[misc]

    def sent(self, kind: type[E]) -> list[E]:
        """What the flow handed ``record_inbound``, one entry per event id. A turn is replayed
        around its engine calls (every pass records the event, only the last commits), so
        every pass is checked by ``assert_clean``; this keeps the last of each."""
        last: dict[str, E] = {}
        for event in self.inbound:
            if isinstance(event, kind):
                last[event.event_id] = event
        return list(last.values())

    def assert_clean(self, *fragments: str) -> None:
        """No pass of any turn ever handed the store one of the fragments."""
        for event in self.inbound:
            text = event.model_dump_json()
            for fragment in fragments:
                assert fragment not in text, (event.type, fragment)


KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # secret-scan: allow
SPLIT_KEY = (
    "sk-proj-AbCdEfGhIj​KlMnOpQrStUvWxYz0123456789"  # zero-width space inside; secret-scan: allow
)
FULLWIDTH_KEY = (
    "ｓｋ-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # ｓｋ-proj-...; secret-scan: allow
)


def _spy_kit(flow_store: StoreFactory, **kw: Any) -> tuple[FlowKit, SpyStore]:
    spies: list[SpyStore] = []

    def factory(clock: Any, ids: Any) -> Store:
        spy = SpyStore(flow_store(clock, ids))
        spies.append(spy)
        return spy

    kit = make_flow_kit(store_factory=factory, **kw)
    return kit, spies[0]


def _stored_text(event: InboundEvent) -> str:
    return event.model_dump_json()


@pytest.mark.parametrize(
    "secret", [KEY, SPLIT_KEY, FULLWIDTH_KEY], ids=["plain", "zero-width", "fullwidth"]
)
async def test_a_secret_in_a_message_is_never_stored_or_shown_to_the_engine(
    flow_store: StoreFactory, secret: str
) -> None:
    kit, spy = _spy_kit(flow_store)
    d = await kit.driver()
    out = await d.say(f"my key is {secret} please use it")
    assert messages.N_INPUT_CLEANED in codes(out)  # the user is told, in plain words
    spy.assert_clean("AbCdEfGhIj", "KlMnOpQrSt", "0123456789")
    [stored] = spy.sent(UserMessage)
    for fragment in ("AbCdEfGhIj", "KlMnOpQrSt", "0123456789"):
        assert fragment not in _stored_text(stored)
    assert "[REDACTED]" in stored.text and stored.text.endswith("please use it")
    seen_by_engine = " ".join(arg for kind, arg in kit.discovery.calls if kind == "handle")
    assert "AbCdEfGhIj" not in seen_by_engine and "0123456789" not in seen_by_engine
    # the replayed (duplicate) event returns what was stored, never the raw text either
    again = await d.send(UserMessage(event_id=stored.event_id, text=f"my key is {secret}"))
    assert all("AbCdEfGhIj" not in e.model_dump_json() for e in again)


async def test_a_secret_in_a_gate_comment_is_not_stored_in_the_approval_or_shown_to_approvers(
    flow_store: StoreFactory,
) -> None:
    kit, spy = _spy_kit(flow_store)
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    dana = principal("dana", roles=["delivery_lead"])
    await d.decide("plan", "reject", who=dana, comment=f"logo is off, token={KEY}\nadd a map")
    approvals = await kit.peek.approvals(d.session_id)
    comments = [a.comment for a in approvals if a.comment]
    spy.assert_clean("AbCdEfGhIj", "KlMnOpQrSt", "0123456789")
    assert comments and all("AbCdEfGhIj" not in c and "0123456789" not in c for c in comments)
    assert "logo is off" in comments[-1]
    # what the other approvers read in the Plan view
    plan = await kit.service.get_plan(
        principal("pat", roles=["client_product_owner"]), d.session_id
    )
    assert plan is not None and plan.enterprise is not None
    assert all("AbCdEfGhIj" not in c for c in plan.enterprise.comments)
    # the stored inbound decision is clean too
    [decision] = [e for e in spy.sent(GateDecision) if e.comment]
    assert "AbCdEfGhIj" not in _stored_text(decision)


async def test_a_secret_in_an_upload_name_or_text_is_not_stored_or_summarised(
    flow_store: StoreFactory,
) -> None:
    kit, spy = _spy_kit(flow_store)
    seen: list[UploadAdded] = []

    def spy_engine(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UploadAdded):
            seen.append(event)
        return None

    kit.discovery.override = spy_engine
    d = await kit.driver()
    await d.send(
        UploadAdded(
            event_id=d.eid(),
            upload_id="u1",
            filename=f"notes-{SPLIT_KEY}.md",
            media_type="text/markdown",
            size_bytes=40,
            text=f"api_key = {KEY}\nan app for plants",
        )  # fmt: skip
    )
    [stored] = spy.sent(UploadAdded)
    spy.assert_clean("AbCdEfGhIj", "KlMnOpQrSt", "0123456789")
    assert "AbCdEfGhIj" not in _stored_text(stored) and "0123456789" not in _stored_text(stored)
    assert stored.text is not None and "an app for plants" in stored.text
    assert seen and "AbCdEfGhIj" not in seen[0].filename + (seen[0].text or "")  # the engine too


async def test_a_secret_typed_into_something_else_is_not_stored(flow_store: StoreFactory) -> None:
    kit, spy = _spy_kit(flow_store)
    d = await kit.driver()
    await d.say("a fitness tracking app")
    card = (await d.snapshot()).pending_card
    assert card is not None
    await d.send(
        OptionSelected(
            event_id=d.eid(),
            card_instance_id=card.instance_id,
            option_ids=[],
            other_text=f"use {KEY}",
        )
    )
    [stored] = spy.sent(OptionSelected)
    spy.assert_clean("AbCdEfGhIj", "KlMnOpQrSt", "0123456789")
    assert stored.other_text is not None and "AbCdEfGhIj" not in stored.other_text


async def test_text_without_a_secret_is_stored_as_typed(flow_store: StoreFactory) -> None:
    kit, spy = _spy_kit(flow_store)
    d = await kit.driver()
    for text in (
        "a shop for plants",
        "मुझे एक ऐप चाहिए",
        "Hinglish: mujhe ek app chahiye",
        "café ☕ 100%",
    ):
        out = await d.say(text)
        assert messages.N_INPUT_CLEANED not in codes(out)
    assert [e.text for e in spy.sent(UserMessage)] == [
        "a shop for plants",
        "मुझे एक ऐप चाहिए",
        "Hinglish: mujhe ek app chahiye",
        "café ☕ 100%",
    ]


# =========================================================================== M7


class CrashesAfterCommit:
    """A project repo that really commits and then 'dies' (the process never reached the database
    commit that was meant to follow)."""

    def __init__(self, inner: LocalGitProjectRepo) -> None:
        self.inner = inner
        self.crash: BaseException | None = None
        self.calls: list[tuple[int, str]] = []

    async def commit_spec(self, **kw: Any) -> CommitInfo:
        self.calls.append((kw["version"], kw["spec_hash"]))
        info = await self.inner.commit_spec(**kw)
        if self.crash is not None:
            crash, self.crash = self.crash, None
            raise crash
        return info


def _git_tags(repo_dir: str) -> list[str]:
    import subprocess

    out = subprocess.run(
        ["git", "-C", repo_dir, "tag", "--list", "spec-v*"],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(out.stdout.split())


async def test_handoff_replay_after_rollback(tmp_path: Path) -> None:
    """The light path writes the project repo before its transaction commits. When that transaction
    does not commit, the number the repo has seen must not be handed to a *different* spec."""
    repo = CrashesAfterCommit(LocalGitProjectRepo(tmp_path / "projects"))
    kit = make_flow_kit(project_repo=repo)  # type: ignore[arg-type]
    d = await handed_off(kit)
    assert (await d.snapshot()).handed_off_version == 1

    repo.crash = RuntimeError("the process died after the commit")
    await d.say("set design.direction=bold-dark")
    await d.run_job()  # the commit happened, the database commit did not
    assert (await d.snapshot()).state == "HANDED_OFF"  # back where the user was
    versions = await kit.service.list_versions(d.who, d.session_id)
    assert [(v.version, v.status) for v in versions if v.kind == "spec"] == [
        (1, "handed_off"),
        (2, "superseded"),  # reserved and retired: the number is spent
    ]
    assert repo.calls[-1][0] == 2

    # the user asks for something else: a different spec, so a different hash. It must not be
    # refused for reusing a number the repo already holds with another hash.
    await d.say("set design.direction=warm-friendly")
    await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "HANDED_OFF" and snap.handed_off_version == 3
    versions = await kit.service.list_versions(d.who, d.session_id)
    assert [(v.version, v.status) for v in versions if v.kind == "spec"] == [
        (1, "handed_off"),
        (2, "superseded"),
        (3, "handed_off"),
    ]
    spec = await kit.service.get_spec(d.who, d.session_id)
    assert spec is not None and spec.version == 3 and spec.status == "handed_off"
    app_dir = tmp_path / "projects" / "tenant-a" / snap.app_id
    assert _git_tags(str(app_dir)) == ["spec-v1", "spec-v2", "spec-v3"]
    assert repo.calls[-1][0] == 3


async def test_a_hard_crash_mid_handoff_converges_when_the_job_is_recovered(tmp_path: Path) -> None:
    repo = CrashesAfterCommit(LocalGitProjectRepo(tmp_path / "projects"))
    kit = make_flow_kit(project_repo=repo)  # type: ignore[arg-type]
    d = await handed_off(kit)
    await d.say("set design.direction=bold-dark")

    repo.crash = asyncio.CancelledError()  # the process is gone: nothing runs after this point
    with pytest.raises(asyncio.CancelledError):
        await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.active_job is not None  # abandoned mid-way
    assert repo.calls[-1][0] == 2  # version 2 reached the repo

    kit.clock.advance(3600)  # the worker's heartbeat is long stale
    async with kit.store.begin(None) as uow:
        assert await uow.jobs.recover_stuck(older_than_seconds=60) == 1
    assert await kit.service.run_next_job()  # another worker picks the job up and finishes it
    snap = await d.snapshot()
    assert (
        snap.state == "HANDED_OFF" and snap.handed_off_version == 3
    )  # a fresh number, no Conflict
    specs = {
        v.version: v.status
        for v in await kit.service.list_versions(d.who, d.session_id)
        if v.kind == "spec"
    }
    assert specs == {1: "handed_off", 2: "superseded", 3: "handed_off"}
    assert repo.calls[-1][0] == 3


async def test_a_gate_2_approval_replay_after_a_lost_race_still_converges(tmp_path: Path) -> None:
    """The Gate 2 path needs no reservation (the draft row already exists): replaying the same
    (version, hash) is idempotent in the repo."""
    repo = CrashesAfterCommit(LocalGitProjectRepo(tmp_path / "projects"))
    kit = make_flow_kit(project_repo=repo)  # type: ignore[arg-type]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    repo.crash = RuntimeError("died after the commit, before the database commit")
    with pytest.raises(HandoffFailed):
        await d.approve_gate2()
    assert (await d.snapshot()).state == "PLAN_REVIEW"  # nothing was recorded
    events = await d.approve_gate2()  # the user presses the button again
    assert [e.version for e in spec_readies(events)] == [1]
    assert [c[0] for c in repo.calls] == [1, 1]  # the same (version, hash) twice: one commit
