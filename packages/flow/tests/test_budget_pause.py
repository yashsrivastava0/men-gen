"""GAP-5: planning pauses, instead of starting or continuing, when the budget cap is reached.

Spec 9: "Enterprise: budget cap reached: pause before the Plan phase; explain; ask an approver."
The flow asks its ``BudgetGate`` before it starts a run (Gate 1, a change at Gate 2 or after
hand-off, the answer to a builder question) and before each phase of a run in flight. When the
answer is no, nothing starts, the session goes to the existing PAUSED state (never a new one), one
plain notice says why, and ``resume`` (an approver may send it) starts what was held once the budget
allows. Run over both stores.
"""

from __future__ import annotations

import pytest

from mde_contracts.events import (
    CardEvent,
    Notice,
    OutboundEvent,
    StateChanged,
)
from mde_core.errors import NotFound
from mde_flow import bookkeeping, messages
from mde_flow.errors import Forbidden
from mde_flow.testing import FakeBudgetGate, enterprise_policy
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit, principal


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


def codes(events: list[OutboundEvent]) -> list[str]:
    return [e.code for e in events if isinstance(e, Notice)]


def notice_text(events: list[OutboundEvent]) -> str:
    return " ".join(e.text for e in events if isinstance(e, Notice))


async def stored(kit: FlowKit, d: Driver):  # type: ignore[no-untyped-def]
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    return rec


async def active_jobs(kit: FlowKit, d: Driver) -> int:
    return len([j for j in await kit.peek.jobs(d.session_id) if j.state in ("queued", "running")])


# ----------------------------------------------------------------------------- before Gate 1


async def test_guided_pauses_before_the_run_and_says_so_briefly() -> None:
    kit = make_flow_kit(budget_gate=FakeBudgetGate(script=[False]))
    d = await kit.driver()
    await d.to_gate1()
    out = await d.approve_gate1()
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_from == "BRIEF_REVIEW"
    assert snap.paused_for == "budget" and snap.active_job is None
    assert codes(out) == [messages.N_BUDGET_CAP]
    assert notice_text(out) == messages.BUDGET_PAUSED_GUIDED  # brief, no approvers, no numbers
    assert await active_jobs(kit, d) == 0
    assert kit.research.calls == [] and kit.builder.calls == []
    # the machine stayed honest: one pause transition, nothing else
    changes = [(e.from_state, e.to_state) for e in out if isinstance(e, StateChanged)]
    assert changes == [("BRIEF_REVIEW", "PAUSED")]
    brief = (await kit.peek.briefs(d.session_id))[-1]
    assert brief.brief.status == "draft"  # the run never started: not "approved" yet


async def test_enterprise_pause_names_the_approvers_and_never_a_number() -> None:
    kit = make_flow_kit(budget_gate=FakeBudgetGate(script=[False]))
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    out = await d.approve_gate1()
    text = notice_text(out)
    assert text.startswith("The budget for this app has been reached")  # the verdict's own words
    assert "Waiting for: client_product_owner, delivery_lead." in text
    assert not any(ch.isdigit() for ch in text)
    assert (await d.snapshot()).state == "PAUSED"


async def test_resume_stays_paused_while_the_budget_is_out_and_starts_the_run_when_it_is_back() -> (
    None
):
    gate = FakeBudgetGate(script=[False])
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    out = await d.ui("resume")  # the budget is still out
    assert codes(out) == [messages.N_BUDGET_CAP]
    assert (await d.snapshot()).state == "PAUSED"
    assert await active_jobs(kit, d) == 0
    gate.allow(True)
    out = await d.ui("resume")
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "research" and snap.paused_for is None
    assert [(e.from_state, e.to_state) for e in out if isinstance(e, StateChanged)] == [
        ("PAUSED", "BRIEF_REVIEW"),
        ("BRIEF_REVIEW", "PLANNING"),
    ]
    rec = await stored(kit, d)
    assert bookkeeping.BUDGET_HOLD not in rec.discovery.extra
    assert rec.approved_brief_version == rec.brief_version
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"


async def test_an_approver_may_lift_the_pause_but_nobody_else() -> None:
    gate = FakeBudgetGate(script=[False])
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.approve_gate1()
    assert (await d.snapshot()).state == "PAUSED"
    intern = principal("ivy", roles=["intern"])
    with pytest.raises(NotFound):  # holds no role of this session: it does not exist for them
        await d.ui("resume", who=intern)
    gate.allow(True)
    lead = principal("dana", roles=["delivery_lead"])
    await d.ui("resume", who=lead)
    assert (await d.snapshot()).state == "PLANNING"


async def test_an_approver_may_not_pause_or_stop_for_the_owner() -> None:
    kit = make_flow_kit(budget_gate=FakeBudgetGate(script=[False]))
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.approve_gate1()
    lead = principal("dana", roles=["delivery_lead"])
    with pytest.raises(Forbidden):
        await d.ui("archive", who=lead)  # only resume is open to an approver, and only for a hold


async def test_a_users_own_pause_is_not_a_budget_hold_and_an_approver_cannot_lift_it() -> None:
    kit = make_flow_kit()
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.ui("pause")
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_for is None
    with pytest.raises(Forbidden):
        await d.ui("resume", who=principal("dana", roles=["delivery_lead"]))
    await d.ui("resume")
    assert (await d.snapshot()).state == "BRIEF_REVIEW"


async def test_a_gate_that_cannot_answer_never_blocks_the_product() -> None:
    gate = FakeBudgetGate()
    gate.fail = True
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    assert (await d.snapshot()).state == "PLANNING"


async def test_without_a_gate_nothing_is_ever_paused() -> None:
    kit = make_flow_kit()
    kit.service.budget_gate = None
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert kit.budget.calls == []


# ----------------------------------------------------------------------------- a run in flight


async def test_a_run_in_flight_stops_cleanly_between_phases_when_the_gate_turns_false() -> None:
    # answers: Gate 1 approval yes, start of research yes, before the build NO
    gate = FakeBudgetGate(script=[True, True, False])
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_from == "BRIEF_REVIEW"
    assert snap.paused_for == "budget" and snap.active_job is None
    assert len(kit.research.calls) == 1 and kit.builder.calls == []  # research ran, build did not
    assert messages.N_BUDGET_CAP in codes(d.events)
    (job,) = await kit.peek.jobs(d.session_id)
    assert job.state == "cancelled" and job.error == "budget paused"  # ended for good, reason kept
    assert (await kit.peek.specs(d.session_id)) == []
    async with kit.store.begin(d.who.tenant_id) as uow:
        note = await uow.research.latest(d.session_id)
    assert note is not None and note.note.version == 1  # what research found is kept

    # the budget is back: resume starts the run again, and the research is not paid for twice
    gate.allow(True)
    await d.ui("resume")
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert len(kit.builder.calls) == 1
    assert kit.research.calls[-1].rerun_jobs == []  # "nothing to re-run": the old note is reused


async def test_the_run_halts_before_research_when_the_budget_went_between_approval_and_start() -> (
    None
):
    gate = FakeBudgetGate(script=[True, False])  # approval yes, the worker finds it gone
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "PAUSED"
    assert kit.research.calls == [] and kit.builder.calls == []


async def test_research_that_is_off_by_policy_is_not_asked_about_the_budget_twice() -> None:
    gate = FakeBudgetGate()
    kit = make_flow_kit(
        policies=[enterprise_policy(policy_id="no-research", research_enabled=False)],
        budget_gate=gate,
    )
    d = await kit.driver(mode="enterprise", policy_id="no-research")
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert len(gate.calls) == 2  # Gate 1 approval, then once before the build (no research phase)


# ----------------------------------------------------------------------------- changes and answers


async def test_a_change_at_gate_2_is_not_started_when_the_budget_is_out() -> None:
    gate = FakeBudgetGate()
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate2()
    gate.allow(False)
    out = await d.say("set design.direction=bold-dark")
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_from == "PLAN_REVIEW" and snap.active_job is None
    assert notice_text(out).endswith(
        messages.BUDGET_CHANGE_DROPPED.strip()
    )  # says it was not applied
    assert await active_jobs(kit, d) == 0
    gate.allow(True)
    out = await d.ui("resume")
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert d.last_gate("plan") is not None
    assert any(e.type == "gate" for e in out)  # the plan is shown again, nothing was lost


async def test_a_change_after_hand_off_is_not_started_when_the_budget_is_out() -> None:
    gate = FakeBudgetGate()
    kit = make_flow_kit(budget_gate=gate)
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    assert (await d.snapshot()).state == "HANDED_OFF"
    gate.allow(False)
    await d.say("feature+ chat")
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_from == "HANDED_OFF"
    assert await active_jobs(kit, d) == 0
    gate.allow(True)
    await d.ui("resume")
    assert (await d.snapshot()).state == "HANDED_OFF"


async def test_the_answer_to_a_builder_question_waits_for_the_budget() -> None:
    gate = FakeBudgetGate()
    kit = make_flow_kit(ask_slot="pay.model", budget_gate=gate)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build" and snap.pending_card
    gate.allow(False)
    out = await d.tap("A")  # the answer is taken, the run is not started
    snap = await d.snapshot()
    assert snap.state == "PAUSED" and snap.paused_from == "DISCOVERING"
    assert messages.N_BUDGET_CAP in codes(out)
    assert not any(isinstance(e, CardEvent) for e in out)
    gate.allow(True)
    await d.ui("resume")
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "build"
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert "pay.model" in kit.builder.calls[-1].answer_slots  # the held answer reached the builder
