"""Scenario tests: the rows of spec section 9 that belong to the flow controller, and the gate
rules end to end (through ``FlowService`` with the scripted engines, no LLM)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from mde_contracts.calls import ChangeOp
from mde_contracts.engines import DiscoveryRequest, DiscoveryResult
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateDecision,
    GateEvent,
    InboundEvent,
    Notice,
    PlanReady,
    SpecReady,
    StateChanged,
    UserMessage,
)
from mde_core.errors import GateError, NotFound, ValidationFailed
from mde_flow import gates, messages
from mde_flow.errors import Forbidden, HandoffFailed
from mde_flow.models import decode_intent
from mde_flow.testing.builders import enterprise_policy
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit, principal


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every scenario in this module over each store of the matrix (conftest: memory, Postgres)."""


def codes(events: Sequence[object]) -> list[str]:
    return [e.code for e in events if isinstance(e, Notice)]


def spec_ready_events(d: Driver) -> list[SpecReady]:
    return d.of_type(SpecReady)


async def spec_versions(kit: FlowKit, d: Driver) -> list[tuple[int, str]]:
    return [(s.version, s.status) for s in (await kit.peek.specs(d.session_id))]


async def test_the_matrix_really_runs_over_the_store_it_names(
    request: pytest.FixtureRequest,
) -> None:
    kit = make_flow_kit()
    expected = "PostgresStore" if "postgres" in request.node.name else "InMemoryStore"
    assert type(kit.store.inner).__name__ == expected


# ----------------------------------------------------------------------------- typed approval


async def test_typed_yes_with_no_gate_is_not_an_approval() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("yes")  # greeting only: the engine asks what we mean
    await d.say("yes")
    assert (await kit.peek.approvals(d.session_id)) == []
    assert (await d.snapshot()).state == "DISCOVERING"
    await d.say("a fitness app")  # a pending card now exists
    await d.say("yes")  # answers the pending card, still no approval
    assert (await kit.peek.approvals(d.session_id)) == []
    assert (await kit.peek.jobs()) == []


async def test_typed_yes_while_planning_or_after_handoff_is_not_an_approval() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    n = len(await kit.peek.approvals(d.session_id))
    await d.say("yes")  # PLANNING: queued, never an approval
    assert len(await kit.peek.approvals(d.session_id)) == n
    await d.run_job()
    await d.say("build it")  # Gate 2 open: a confident typed approval
    assert [e.version for e in spec_ready_events(await refreshed(d))] == [1]
    await d.say("yes")  # HANDED_OFF: no gate
    assert len(await kit.peek.approvals(d.session_id)) == n + 1  # only the one typed approval


async def refreshed(d: Driver) -> Driver:
    await d.refresh()
    return d


async def test_typed_approval_at_an_open_gate_counts_and_records_typed() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.say("yes, build it")
    recs = await kit.peek.approvals(d.session_id)
    assert [(r.gate, r.decision, r.via, r.role) for r in recs] == [
        ("brief", "approve", "typed", "requester")
    ]
    assert (await d.snapshot()).state == "PLANNING"


async def test_tap_approval_records_tap() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    assert [r.via for r in (await kit.peek.approvals(d.session_id))] == ["tap"]


async def test_unclear_approval_at_a_gate_shows_the_buttons_again() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    events = await d.say("ok maybe")
    assert (await kit.peek.approvals(d.session_id)) == []
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    again = [e for e in events if isinstance(e, GateEvent)]
    assert len(again) == 1 and again[0].hash == gate1.hash and not again[0].single_button
    assert messages.N_NOT_AN_APPROVAL in codes(events)


async def test_unclear_approval_with_a_change_applies_the_change_only() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    events = await d.say("looks fine but set design.direction=bold-dark")
    assert (await kit.peek.approvals(d.session_id)) == []
    new = [e for e in events if isinstance(e, GateEvent)][-1]
    assert new.version == gate1.version + 1 and new.hash != gate1.hash
    assert new.single_button and any("bold-dark" in line for line in new.diff_lines)


# ----------------------------------------------------------------------------- hash binding


async def test_approve_then_immediately_change_voids_the_approval() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    await d.approve_gate1()
    assert (await d.snapshot()).state == "PLANNING"  # queued, nothing has run yet
    events = await d.say("set design.direction=bold-dark")
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW"
    gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert gate.version == 2 and gate.hash != gate1.hash
    assert gate.single_button and gate.approved_roles == []  # v1's approval does not carry over
    assert any(isinstance(e, AssistantMessage) and "no longer applies" in e.text for e in events)
    (job,) = await kit.peek.jobs(d.session_id)
    assert job.state == "cancelled"
    # approving the NEW version starts a fresh run (the cancelled one does not block it)
    await d.decide("brief", "approve")
    assert (await d.snapshot()).state == "PLANNING"
    assert [j.state for j in (await kit.peek.jobs(d.session_id))] == ["cancelled", "queued"]


async def test_hash_mismatch_never_approves_a_stale_version() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    await d.say("set design.direction=bold-dark")  # v2
    events = await d.decide("brief", "approve", version=gate1.version, hash=gate1.hash)
    assert messages.N_STALE_GATE in codes(events)
    assert (await kit.peek.approvals(d.session_id)) == []
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    shown = [e for e in events if isinstance(e, GateEvent)][-1]
    assert shown.version == 2 and shown.single_button and shown.diff_lines
    # right version, wrong hash
    events = await d.decide("brief", "approve", version=2, hash="0" * 64)
    assert messages.N_STALE_GATE in codes(events)
    assert (await kit.peek.approvals(d.session_id)) == []


async def test_gate_decision_when_no_gate_is_open_is_a_friendly_notice() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    events = await d.decide("brief", "approve", version=1, hash="1" * 64)
    assert codes(events) == [messages.N_GATE_CLOSED]
    assert (await d.snapshot()).state == "DISCOVERING"


async def test_gate_left_open_never_auto_approves() -> None:
    kit = make_flow_kit(policies=[enterprise_policy(policy_id="ent-r", reminder_hours=24)])
    d = await kit.driver(mode="enterprise", policy_id="ent-r")
    await d.to_gate1()
    kit.clock.advance(60 * 60 * 24 * 30)
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW" and snap.gate is not None
    assert snap.gate.status.status == "open" and (await kit.peek.approvals(d.session_id)) == []
    assert not (await kit.peek.jobs())
    # Enterprise: a reminder was scheduled for the approver role, nothing approved
    assert kit.reminders.scheduled and kit.reminders.scheduled[0].roles == ("requester",)


# ----------------------------------------------------------------------------- planning


async def test_typing_during_planning_is_queued_and_applied_at_gate_2() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    assert (await d.snapshot()).state == "PLANNING"
    events = await d.say("set design.direction=bold-dark")
    assert any(isinstance(e, AssistantMessage) and e.text == messages.QUEUED for e in events)
    assert (await d.snapshot()).queued_messages == 1
    assert (await d.snapshot()).state == "PLANNING"  # the run was not disturbed
    hold.release()
    await task
    await d.refresh()
    # Gate 2 for v1 opened, then the queued note became a change run (v2 on the way)
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.queued_messages == 0
    assert any(isinstance(e, PlanReady) and e.version == 1 for e in d.events)
    assert await d.run_job()
    await d.refresh()
    gate2 = d.last_gate("plan")
    assert gate2 is not None and gate2.version == 2 and gate2.single_button
    assert any("look" in line.lower() for line in gate2.diff_lines)
    assert [(s.version, s.status) for s in (await kit.peek.specs(d.session_id))] == [
        (1, "superseded"),
        (2, "draft"),
    ]


async def test_queued_messages_are_redacted_and_capped() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    await d.say("my key is sk-" + "a" * 40)
    rec = (await kit.peek.sessions())[0]
    assert "sk-" + "a" * 40 not in rec.queued_messages[0]
    for i in range(30):
        await d.say(f"note {i}")
    assert len((await kit.peek.sessions())[0].queued_messages) == 25
    hold.release()
    await task


async def test_change_of_app_type_during_planning_cancels_the_run() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    (job,) = await kit.peek.jobs(d.session_id)
    token = kit.service.runs.token(job.id)
    events = await d.say("set idea.archetype=booking")
    assert token.cancelled
    assert any(isinstance(e, StateChanged) and e.from_state == "PLANNING" for e in events)
    snap = await d.snapshot()
    # Discover restarted at that card and, nothing else being missing, the new Brief is up again
    assert snap.state == "BRIEF_REVIEW" and snap.plan_phase is None
    new_gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert new_gate.version == gate1.version + 1 and new_gate.single_button
    hold.release()
    await task  # the cancelled run winds down quietly
    assert (await kit.peek.jobs(d.session_id))[0].state == "cancelled"
    assert (await kit.peek.specs(d.session_id)) == []


async def test_stop_during_planning_returns_to_origin() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    events = await d.ui("stop")
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert any(isinstance(e, GateEvent) and e.gate == "brief" for e in events)  # where they were
    hold.release()
    await task
    assert [j.state for j in (await kit.peek.jobs(d.session_id))] == ["cancelled"]
    assert (await kit.peek.specs(d.session_id)) == []
    n_events = len(await kit.peek.outbound(d.session_id))
    await d.refresh()
    assert not any(isinstance(e, PlanReady) for e in d.events)
    assert len(await kit.peek.outbound(d.session_id)) == n_events  # nothing after the Stop


async def test_typed_stop_during_planning_returns_to_origin() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    await d.say("stop")
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    hold.release()
    await task


async def test_stop_of_a_change_run_returns_to_gate_2() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.say("set design.direction=bold-dark")  # change run from PLAN_REVIEW
    assert (await d.snapshot()).origin == "PLAN_REVIEW"
    hold = kit.builder.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    await d.ui("stop")
    snap = await d.snapshot()
    assert snap.state == "PLAN_REVIEW" and snap.gate is not None and snap.gate.version == 1
    hold.release()
    await task
    assert [s.version for s in (await kit.peek.specs(d.session_id))] == [1]


async def test_stop_after_handoff_change_returns_to_handed_off() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    await d.say("set design.direction=bold-dark")
    hold = kit.builder.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    await d.ui("stop")
    assert (await d.snapshot()).state == "HANDED_OFF"
    hold.release()
    await task
    assert len(spec_ready_events(await refreshed(d))) == 1  # only the original hand-off


async def test_stop_when_nothing_runs_is_a_friendly_notice() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    assert codes(await d.ui("stop")) == [messages.N_NOTHING_TO_STOP]


async def test_research_failure_continues_from_defaults() -> None:
    kit = make_flow_kit()
    kit.research.modes = ["raise"]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    assert messages.N_RESEARCH_UNAVAILABLE in codes(d.events)
    gate2 = d.last_gate("plan")
    assert gate2 is not None
    assert gate2.page.kind == "plan" and gate2.page.plan.research_status == "skipped"
    note = await kit.service.get_spec(d.who, d.session_id)
    assert note is not None
    async with kit.store.begin("tenant-a") as uow:
        saved = await uow.research.latest(d.session_id)
    assert saved is not None and saved.note.status == "unavailable"


async def test_research_reported_unavailable_continues_too() -> None:
    kit = make_flow_kit()
    kit.research.modes = ["unavailable"]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    assert messages.N_RESEARCH_UNAVAILABLE in codes(d.events)
    assert d.last_gate("plan") is not None


async def test_research_off_by_policy_is_skipped_and_marked_unavailable() -> None:
    kit = make_flow_kit(
        policies=[enterprise_policy(policy_id="no-research", research_enabled=False)]
    )
    d = await kit.driver(mode="enterprise", policy_id="no-research")
    await d.to_gate1()
    await d.decide("brief", "approve")
    await d.run_job()
    assert kit.research.calls == []
    async with kit.store.begin("tenant-a") as uow:
        saved = await uow.research.latest(d.session_id)
    assert saved is not None and saved.note.unavailable_reason == "policy"


async def test_needs_answer_goes_back_to_discover_and_resumes_the_builder() -> None:
    kit = make_flow_kit(ask_slot="pay.model")
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build" and snap.pending_card
    assert snap.pending_card.card.slot == "pay.model"
    assert any(isinstance(e, CardEvent) and e.card.card.slot == "pay.model" for e in d.events)
    assert [j.state for j in (await kit.peek.jobs(d.session_id))] == ["succeeded"]
    await d.tap("A")  # answer the question
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "build"
    await d.run_job()
    await d.refresh()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert len(kit.research.calls) == 1  # research was not repeated
    last = kit.builder.calls[-1]
    assert "pay.model" in last.answer_slots
    assert last.brief.hash == kit.builder.calls[0].brief.hash  # still the approved Brief


async def test_needs_answer_survives_pause_resume() -> None:
    kit = make_flow_kit(ask_slot="pay.model")
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.ui("pause")
    assert (await d.snapshot()).state == "PAUSED"
    events = await d.ui("resume")
    assert any(isinstance(e, CardEvent) for e in events)  # the question is shown again
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build"


async def test_build_failure_returns_to_origin_with_a_friendly_notice_and_can_retry() -> None:
    kit = make_flow_kit()
    kit.builder.modes = ["raise"]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.refresh()
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert messages.N_BUILD_FAILED in codes(d.events)
    (job,) = await kit.peek.jobs(d.session_id)
    assert job.state == "cancelled" and job.error == "failed"  # ended for good, reason kept
    # tap Looks right again: the approval is still complete, the run starts again
    await d.decide("brief", "approve")
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"


async def test_builder_hash_that_does_not_match_its_text_is_refused() -> None:
    kit = make_flow_kit()
    kit.builder.modes = ["invalid_hash"]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert (await kit.peek.specs(d.session_id)) == []


# ----------------------------------------------------------------------------- gate 2 / hand-off


async def test_spec_ready_only_after_gate_2_approval_of_the_current_hash() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate2 = await d.to_gate2()
    assert spec_ready_events(d) == [] and kit.frontend.notifications == []
    # a stale/garbage decision does nothing
    await d.decide("plan", "approve", version=gate2.version, hash="f" * 64)
    assert spec_ready_events(d) == [] and kit.fake_repo.commits == []
    await d.approve_gate2()
    assert len(spec_ready_events(d)) == 1 and len(kit.fake_repo.commits) == 1


async def test_change_at_gate_2_makes_a_new_version_and_voids_the_old_approval() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate2 = await d.to_gate2()
    events = await d.say("set design.direction=bold-dark")
    assert (await d.snapshot()).state == "PLANNING"
    assert any(isinstance(e, StateChanged) and e.to_state == "PLANNING" for e in events)
    await d.run_job()
    await d.refresh()
    new = d.last_gate("plan")
    assert new is not None and new.version == gate2.version + 1 and new.hash != gate2.hash
    assert new.single_button and new.diff_lines
    stale = await d.decide("plan", "approve", version=gate2.version, hash=gate2.hash)
    assert messages.N_STALE_GATE in codes(stale) and not spec_ready_events(d)
    await d.approve_gate2()
    (ready,) = spec_ready_events(d)
    assert ready.version == new.version and ready.hash == new.hash
    assert [(s.version, s.status) for s in (await kit.peek.specs(d.session_id))] == [
        (1, "superseded"),
        (2, "handed_off"),
    ]


async def test_research_toggle_at_gate_2_reruns_only_the_builder() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate2 = await d.to_gate2()
    assert [a.id for a in gate2.page.plan.research_added] == ["RA-001", "RA-002"]  # type: ignore[union-attr]
    await d.ui("toggle_research_item", "RA-001", False)
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    await d.refresh()
    new = d.last_gate("plan")
    assert new is not None and new.version == 2
    added = {a.id: a.included for a in new.page.plan.research_added}  # type: ignore[union-attr]
    assert added == {"RA-001": False, "RA-002": True}
    assert len(kit.research.calls) == 1  # the Note was reused
    assert kit.builder.calls[-1].excluded_research_ids == ["RA-001"]
    assert kit.builder.calls[-1].affected_sections == ["screens", "navigation", "data", "api"]


async def test_stale_research_note_is_refreshed_when_planning_runs_again() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    kit.clock.advance(60 * 60 * 24 * 15)  # older than the 14 day staleness limit
    await d.say("set design.direction=bold-dark")
    await d.run_job()
    assert len(kit.research.calls) == 2


async def test_scope_change_at_gate_2_goes_back_to_discover() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    events = await d.say("set idea.archetype=booking")
    assert (await d.snapshot()).state == "BRIEF_REVIEW"  # Discover, then a new Brief for Gate 1
    gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert gate.gate == "brief" and gate.version == 2
    assert (await kit.peek.jobs(d.session_id))[-1].state != "running"


async def test_handoff_failure_leaves_the_gate_open_and_can_be_retried() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    kit.fake_repo.fail_next = 1
    with pytest.raises(HandoffFailed):
        await d.approve_gate2()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert (await kit.peek.approvals(d.session_id))[
        -1
    ].gate == "brief"  # no plan approval persisted
    assert kit.frontend.notifications == []
    assert [s.status for s in (await kit.peek.specs(d.session_id))] == ["draft"]
    await d.approve_gate2()
    assert (await d.snapshot()).state == "HANDED_OFF" and len(kit.frontend.notifications) == 1


async def test_frontend_notify_failure_is_reported_but_spec_ready_stands() -> None:
    kit = make_flow_kit()

    async def boom(event: SpecReady) -> None:
        raise RuntimeError("down")

    kit.frontend.notify = boom  # type: ignore[method-assign]
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    await d.refresh()
    assert len(spec_ready_events(d)) == 1
    assert messages.N_HANDOFF_NOTIFY_FAILED in codes(d.events)
    assert (await d.snapshot()).state == "HANDED_OFF"


async def test_a_second_handoff_commit_for_the_same_version_is_idempotent() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    info = await kit.fake_repo.commit_spec(
        tenant_id="tenant-a", app_id=(await d.snapshot()).app_id, spec_text="x", version=1,
        spec_hash=kit.fake_repo.commits[0].spec_hash, message="again",
    )  # fmt: skip
    assert info.sha == kit.fake_repo.commits[0].sha and len(kit.fake_repo.commits) == 1


# ----------------------------------------------------------------------------- post-handoff


async def test_light_post_handoff_change_applies_directly_without_a_gate() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    events = await d.say("set design.direction=bold-dark")
    assert (await d.snapshot()).state == "PLANNING"
    assert not [e for e in events if isinstance(e, GateEvent)]
    await d.run_job()
    await d.refresh()
    snap = await d.snapshot()
    assert snap.state == "HANDED_OFF" and snap.handed_off_version == 2
    ready = spec_ready_events(d)
    assert [r.version for r in ready] == [1, 2]
    assert kit.frontend.notifications == ready
    assert [g.gate for g in d.of_type(GateEvent)] == ["brief", "plan"]  # no second Gate 2
    light = [
        a for a in (await kit.peek.approvals(d.session_id)) if a.gate == "plan" and a.version == 2
    ]
    assert len(light) == 1 and light[0].comment == gates.LIGHT_CHANGE_COMMENT
    assert light[0].role == "requester" and light[0].via == "typed"
    spec = await kit.service.get_spec(d.who, d.session_id, 2)
    assert spec is not None and spec.status == "handed_off" and spec.spec.revision.changed_ids
    assert spec.spec.revision.previous_version == 1
    old = await kit.service.get_spec(d.who, d.session_id, 1)
    # a handed-off version is history (the store never moves it to superseded); the session's
    # handed_off_version names the current one
    assert old is not None and old.status == "handed_off"
    assert (await d.snapshot()).handed_off_version == 2


async def test_heavy_post_handoff_change_reopens_gate_2() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    await d.say("set auth.method=sso")  # auth is in heavy_if
    await d.run_job()
    await d.refresh()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert len(spec_ready_events(d)) == 1  # not yet
    gate = d.last_gate("plan")
    assert gate is not None and gate.version == 2 and gate.single_button
    old = await kit.service.get_spec(d.who, d.session_id, 1)
    assert old is not None and old.status == "handed_off"  # stays until the new one is approved
    await d.approve_gate2()
    assert [r.version for r in spec_ready_events(d)] == [1, 2]
    assert (await kit.service.get_spec(d.who, d.session_id, 1)).status == "handed_off"  # type: ignore[union-attr]
    assert (await d.snapshot()).handed_off_version == 2


async def test_two_new_screens_at_once_exceed_the_light_limit() -> None:
    policy = enterprise_policy(
        policy_id="few", plan_approvers=("requester",), heavy_if=("auth",), max_new_screens_light=1
    )
    kit = make_flow_kit(policies=[policy])
    d = await kit.driver(mode="enterprise", policy_id="few")
    await d.to_gate1()
    await d.decide("brief", "approve")
    await d.run_job()
    await d.decide("plan", "approve")

    # one change that adds two features at once -> two new screens > 1 -> heavy
    kit.discovery.override = _two_features
    await d.say("add two")
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"


def _two_features(req: DiscoveryRequest, ev: InboundEvent) -> DiscoveryResult | None:
    if not (isinstance(ev, UserMessage) and ev.text == "add two"):
        return None
    ops = [
        ChangeOp(kind="toggle_feature", target_id="chat", value_bool=True),
        ChangeOp(kind="toggle_feature", target_id="map", value_bool=True),
    ]
    slots = dict(req.state.slots)
    from mde_flow.testing.builders import slot

    slots["scope.features"] = slot("scope.features", ["browse", "search", "chat", "map"])
    state = req.state.model_copy(update={"slots": slots})
    return DiscoveryResult(
        state=state, change_ops=ops, changed_slots=["scope.features"], brief_complete=True,
        intent="change",
    )  # fmt: skip


async def test_a_repair_in_a_light_change_escalates_to_gate_2() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    kit.builder.modes = ["repair"]
    await d.say("set design.direction=bold-dark")
    await d.run_job()
    # rule 4/8: the engine repaired something, so it needs the tap
    assert (await d.snapshot()).state == "PLAN_REVIEW"


async def test_post_handoff_research_refresh_makes_the_change_heavy() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    kit.clock.advance(60 * 60 * 24 * 20)
    await d.say("set design.direction=bold-dark")
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert len(kit.research.calls) == 2


async def test_light_change_toggled_by_tap_records_a_tap_approval() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    await d.ui("toggle_assumption", "design.direction", True)  # a design change: light
    await d.run_job()
    rec = [
        a
        for a in (await kit.peek.approvals(d.session_id))
        if a.comment == gates.LIGHT_CHANGE_COMMENT
    ]
    assert rec and rec[0].via == "tap"


async def test_decoded_job_intent_round_trips() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.say("set design.direction=bold-dark")
    (_, job) = await kit.peek.jobs(d.session_id)
    intent = decode_intent(job.payload.change_ops_json)
    assert [o.slot_id for o in intent.ops] == ["design.direction"]
    assert job.payload.affected_sections == ["design"] and job.payload.start_phase == "build"


# ----------------------------------------------------------------------------- Gate 1 edits


async def test_edit_row_reopens_the_card_and_returns_with_a_diff() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    events = await d.ui("edit_row", "look")
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.pending_card
    assert snap.pending_card.card.slot == "design.direction"
    assert any(isinstance(e, CardEvent) for e in events)
    events = await d.tap("B")  # bold-dark
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW"
    gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert gate.version == gate1.version + 1 and gate.single_button
    assert any("bold-dark" in line for line in gate.diff_lines)


async def test_toggle_assumption_at_gate_1_changes_the_brief() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    assert [a.slot_id for a in gate1.page.brief.assumptions] == ["scope.features"]  # type: ignore[union-attr]
    events = await d.ui("toggle_assumption", "scope.features", False)
    gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert gate.version == 2 and gate.page.brief.assumptions == []  # type: ignore[union-attr]
    assert any("No longer assumed" in line for line in gate.diff_lines)


async def test_toggle_feature_at_gate_1_changes_the_features_row() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    events = await d.ui("toggle_feature", "chat", True)
    gate = [e for e in events if isinstance(e, GateEvent)][-1]
    assert any("Feature added: Chat" in line for line in gate.diff_lines)


# ----------------------------------------------------------------------------- Enterprise approvers


def _approver(name: str, role: str):  # type: ignore[no-untyped-def]
    return principal(name, roles=[role])


async def _enterprise_to_gate2(kit: FlowKit) -> Driver:
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    await d.decide("brief", "approve")  # the requester is the only Gate 1 approver
    await d.run_job()
    await d.refresh()
    assert d.last_gate("plan") is not None
    return d


async def test_enterprise_build_needs_every_required_role_on_the_same_hash() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    gate = d.last_gate("plan")
    assert gate is not None and gate.required_roles == ["client_product_owner", "delivery_lead"]
    assert gate.buttons[0] == "Send for approval"
    owner = await d.decide("plan", "approve")  # the requester submits; they are not an approver
    assert (
        messages.N_SUBMITTED in codes(owner)
        and (await kit.peek.approvals(d.session_id))[-1].gate == "brief"
    )
    po = _approver("pat", "client_product_owner")
    out = await d.decide("plan", "approve", who=po)
    assert messages.N_WAITING_APPROVERS in codes(out) and not spec_ready_events(d)
    shown = [e for e in out if isinstance(e, GateEvent)][-1]
    assert shown.approved_roles == ["client_product_owner"]
    assert kit.fake_repo.commits == [] and (await d.snapshot()).state == "PLAN_REVIEW"
    dl = _approver("dana", "delivery_lead")
    out = await d.decide("plan", "approve", who=dl)
    assert len([e for e in out if isinstance(e, SpecReady)]) == 1
    assert (await d.snapshot()).state == "HANDED_OFF"


async def test_enterprise_split_approvers_do_not_start_the_build_and_comments_become_changes() -> (
    None
):
    # the delivery lead outranks the requester, so their comment may change the look the
    # requester picked (without a precedence it would be a disagreement for the owner to settle)
    kit = make_flow_kit(
        policies=[
            enterprise_policy(
                policy_id="enterprise-default",
                role_precedence=["delivery_lead", "client_product_owner", "requester"],
            )
        ]
    )
    d = await _enterprise_to_gate2(kit)
    gate = d.last_gate("plan")
    assert gate is not None
    po, dl = _approver("pat", "client_product_owner"), _approver("dana", "delivery_lead")
    await d.decide("plan", "approve", who=po)
    out = await d.decide(
        "plan", "reject", who=dl, comment="set design.direction=bold-dark\nfeature+ chat"
    )
    assert not [e for e in out if isinstance(e, SpecReady)] and kit.fake_repo.commits == []
    # each comment became a change request: one change run covering both
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    await d.refresh()
    new = d.last_gate("plan")
    assert new is not None and new.version == 2 and new.approved_roles == []  # approvals void
    assert new.single_button
    # po's approval of v1 does not count for v2; both must approve v2
    out = await d.decide("plan", "approve", who=po)
    assert not [e for e in out if isinstance(e, SpecReady)]
    out = await d.decide("plan", "approve", who=dl)
    assert [e.version for e in out if isinstance(e, SpecReady)] == [2]


async def test_rejection_without_comments_changes_nothing() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    dl = _approver("dana", "delivery_lead")
    out = await d.decide("plan", "reject", who=dl)
    assert messages.N_NOT_UNDERSTOOD in codes(out)
    assert (await d.snapshot()).state == "PLAN_REVIEW" and not kit.fake_repo.commits


async def test_a_reject_revokes_an_earlier_approval_of_the_same_role() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    po, dl = _approver("pat", "client_product_owner"), _approver("dana", "delivery_lead")
    await d.decide("plan", "approve", who=po)
    await d.decide("plan", "reject", who=po)
    out = await d.decide("plan", "approve", who=dl)  # dl alone is not enough now
    assert not [e for e in out if isinstance(e, SpecReady)]
    out = await d.decide("plan", "approve", who=po)  # po changes their mind: complete
    assert [e.version for e in out if isinstance(e, SpecReady)] == [1]


async def test_a_stranger_does_not_find_the_session_and_an_approver_may_only_decide() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    eve = principal("eve", roles=["janitor"])  # holds no role of this session's gates
    with pytest.raises(NotFound):  # same tenant, no role: the session is simply not there
        await d.decide("plan", "approve", who=eve)
    with pytest.raises(NotFound):
        await kit.service.get_snapshot(eve, d.session_id)
    with pytest.raises(Forbidden):  # an approver knows the session exists but may not chat
        await d.send(
            UserMessage(event_id=d.eid(), text="hello"),
            who=_approver("pat", "client_product_owner"),
        )


async def test_an_approver_cannot_act_as_a_role_they_do_not_hold() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    with pytest.raises(GateError):
        await d.decide(
            "plan", "approve", who=_approver("pat", "client_product_owner"), role="delivery_lead"
        )
    shown = d.last_gate("plan")
    assert shown is not None
    spoof = GateDecision(
        event_id=d.eid(), gate="plan", decision="approve", version=shown.version, hash=shown.hash,
        approver_id="someone-else",
    )  # fmt: skip
    with pytest.raises(GateError):  # approver_id must be the authenticated user
        await d.send(spoof, who=_approver("pat", "client_product_owner"))


async def test_enterprise_plan_view_shows_approver_status() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    out = await d.decide("plan", "approve", who=_approver("pat", "client_product_owner"))
    shown = [e for e in out if isinstance(e, GateEvent)][-1]
    extras = shown.page.plan.enterprise  # type: ignore[union-attr]
    assert extras is not None and extras.full_file_view
    assert [(a.role, a.decision) for a in extras.approvers] == [
        ("client_product_owner", "approve"),
        ("delivery_lead", None),
    ]


async def test_enterprise_reminders_are_scheduled_and_cancelled() -> None:
    kit = make_flow_kit()
    d = await _enterprise_to_gate2(kit)
    scheduled = [r for r in kit.reminders.scheduled if r.gate == "plan"]
    assert scheduled and scheduled[0].roles == ("client_product_owner", "delivery_lead")
    await d.decide("plan", "approve", who=_approver("pat", "client_product_owner"))
    await d.decide("plan", "approve", who=_approver("dana", "delivery_lead"))
    assert (kit.reminders.cancelled[-1][2]) == "plan"


# ----------------------------------------------------------------------------- idempotency etc.


async def test_a_duplicate_inbound_event_is_not_processed_twice() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    ev_id = d.eid()
    first = await d.decide("brief", "approve", event_id=ev_id)
    approvals = len(await kit.peek.approvals(d.session_id))
    jobs = len(await kit.peek.jobs(d.session_id))
    again = await d.decide(
        "brief", "approve", version=gate1.version, hash=gate1.hash, event_id=ev_id
    )
    assert [e.event_id for e in again] == [e.event_id for e in first]
    assert len(await kit.peek.approvals(d.session_id)) == approvals
    assert len(await kit.peek.jobs(d.session_id)) == jobs


async def test_outbound_events_have_strictly_increasing_seq_per_session() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    await d.refresh()
    seqs = [e.seq for e in (await kit.peek.outbound(d.session_id))]
    assert seqs == list(range(1, len(seqs) + 1))
    assert (await d.snapshot()).last_seq == len(seqs)


async def test_engines_cannot_emit_gates_or_spec_ready() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    forged = SpecReady(
        event_id="e", session_id=d.session_id, app_id="a", version=9, hash="h", git_sha="s",
        path="APP_SPEC.md", repo_dir=".",
    )  # fmt: skip

    def override(req: DiscoveryRequest, ev: InboundEvent) -> DiscoveryResult | None:
        return DiscoveryResult(state=req.state, events=[forged])

    kit.discovery.override = override
    events = await d.say("anything")
    assert not [e for e in events if isinstance(e, SpecReady)]
    assert kit.frontend.notifications == []


async def test_pause_and_resume_return_to_where_the_user_was() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    gate1 = await d.to_gate1()
    await d.ui("pause")
    assert (await d.snapshot()).state == "PAUSED"
    out = await d.decide("brief", "approve")
    assert codes(out) == [messages.N_PAUSED] and (await kit.peek.approvals(d.session_id)) == []
    out = await d.ui("resume")
    assert [e.hash for e in out if isinstance(e, GateEvent)] == [gate1.hash]
    assert (await d.snapshot()).state == "BRIEF_REVIEW"


async def test_pause_while_planning_is_refused_with_a_notice() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    assert codes(await d.ui("pause")) == [messages.N_CANNOT_PAUSE]
    assert (await d.snapshot()).state == "PLANNING"
    assert codes(await d.say("pause")) == [messages.N_CANNOT_PAUSE]


async def test_restart_archives_and_cancels_a_running_plan() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    task = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    out = await d.ui("restart")
    assert messages.N_ARCHIVED in codes(out)
    assert (await d.snapshot()).state == "ARCHIVED"
    hold.release()
    await task
    assert (await kit.peek.jobs(d.session_id))[0].state == "cancelled"
    assert codes(await d.say("hello")) == [messages.N_ARCHIVED]  # nothing more happens


async def test_typed_start_over_archives_the_session() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.say("start over")
    assert (await d.snapshot()).state == "ARCHIVED"


async def test_tenant_isolation_and_unknown_sessions() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    other = principal("mallory", tenant_id="tenant-b")
    with pytest.raises(NotFound):
        await kit.service.get_snapshot(other, d.session_id)
    with pytest.raises(NotFound):
        await kit.service.handle_event(other, d.session_id, UserMessage(event_id="e", text="hi"))
    with pytest.raises(NotFound):
        await kit.service.handle_event(d.who, "ses_missing", UserMessage(event_id="e", text="hi"))


async def test_create_session_validates_the_policy_mode() -> None:
    kit = make_flow_kit()
    with pytest.raises(ValidationFailed):
        await kit.service.create_session(principal(), "guided", "enterprise-default")
    with pytest.raises(NotFound):
        await kit.service.create_session(principal(), "guided", "no-such-policy")


async def test_read_models_versions_brief_plan_spec() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.say("set design.direction=bold-dark")
    await d.run_job()
    versions = await kit.service.list_versions(d.who, d.session_id)
    assert [(v.kind, v.version, v.status) for v in versions] == [
        ("brief", 1, "approved"),
        ("spec", 1, "superseded"),
        ("spec", 2, "draft"),
    ]
    brief = await kit.service.get_brief(d.who, d.session_id)
    assert brief is not None and brief.version == 1 and brief.hash
    assert (await kit.service.get_brief(d.who, d.session_id, 1)) == brief
    plan = await kit.service.get_plan(d.who, d.session_id)
    assert plan is not None and plan.spec_version == 2
    first = await kit.service.get_plan(d.who, d.session_id, 1)
    assert first is not None and first.spec_version == 1
    assert await kit.service.get_plan(d.who, d.session_id, 9) is None
    assert await kit.service.get_spec(d.who, d.session_id, 99) is None
    events = await kit.service.list_events(d.who, d.session_id, after_seq=3, limit=2)
    assert [e.seq for e in events] == [4, 5]


async def test_a_gate_decision_with_a_change_comment_applies_the_change() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    out = await d.decide("brief", "change", comment="set design.direction=bold-dark")
    gate = [e for e in out if isinstance(e, GateEvent)][-1]
    assert gate.version == 2 and gate.single_button
    out = await d.decide("brief", "change")  # no comment: ask what to change
    assert any(
        isinstance(e, AssistantMessage) and e.text == messages.GATE_CHANGE_PROMPT for e in out
    )


async def test_a_change_that_changes_nothing_in_the_spec_is_acknowledged() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    out = await d.ui("toggle_assumption", "approvers", True)
    assert messages.N_NO_CHANGE in codes(out)
    assert (await d.snapshot()).state == "PLAN_REVIEW"
