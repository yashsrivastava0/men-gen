"""More flow scenarios: failure paths, odd inputs, suspended runs, read models."""

from __future__ import annotations

import pytest

from mde_contracts.engines import BuildRequest, BuildResult, ResearchRequest, RunContext
from mde_contracts.events import (
    CardEvent,
    GateEvent,
    Notice,
    OptionSelected,
    UiAction,
    UploadAdded,
    UserMessage,
)
from mde_contracts.records import JobRecord, PlanJobPayload
from mde_contracts.research import ResearchNote
from mde_core.cancel import CancellationToken
from mde_core.errors import BudgetExceeded
from mde_flow import messages
from mde_flow.testing.kit import Driver, make_flow_kit, principal


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every scenario in this module over each store of the matrix (conftest: memory, Postgres)."""


def codes(events: object) -> list[str]:
    return [e.code for e in events if isinstance(e, Notice)]  # type: ignore[attr-defined]


async def at_gate1() -> Driver:
    d = await make_flow_kit().driver()
    await d.to_gate1()
    return d


async def test_running_a_job_nobody_owns_is_a_quiet_no_op() -> None:
    kit = make_flow_kit()
    ghost = JobRecord(
        id="job_missing",
        session_id="ses_missing",
        tenant_id="tenant-a",
        payload=PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1),
    )
    await kit.service.run_plan_job(ghost, "w", CancellationToken())  # nothing to do, no error
    assert await kit.service.run_next_job() is False  # an empty queue is just False


async def test_an_unexpected_error_in_a_job_leaves_the_session_usable_then_surfaces() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    real = kit.service._present_gate

    async def explodes_at_gate_2(turn, gate, **kw):  # type: ignore[no-untyped-def]
        if gate == "plan":
            raise RuntimeError("something nobody planned for")
        return await real(turn, gate, **kw)

    kit.service._present_gate = explodes_at_gate_2  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await d.run_job()
    await d.refresh()
    assert (await d.snapshot()).state == "BRIEF_REVIEW"  # the user is back where they were
    assert messages.N_BUILD_FAILED in codes(d.events)
    (job,) = await kit.peek.jobs(d.session_id)
    assert job.state == "cancelled" and job.error == "failed"  # ended for good, reason kept


async def test_a_question_without_a_card_is_a_failed_build() -> None:
    kit = make_flow_kit()

    async def build(req: BuildRequest, ctx: RunContext) -> BuildResult:
        return BuildResult(status="needs_answer")

    kit.builder.build = build  # type: ignore[method-assign]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "BRIEF_REVIEW"


async def test_a_reply_that_changes_the_app_type_drops_the_suspended_run() -> None:
    kit = make_flow_kit(ask_slot="pay.model")
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build"
    await d.say("set idea.archetype=booking")  # the app itself changes, the question is moot
    snap = await d.snapshot()
    assert snap.plan_phase is None and snap.state in {"DISCOVERING", "BRIEF_REVIEW"}
    await d.tap("A")  # answering the stale question just fills a slot
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW" and snap.brief_version == 2
    assert not [j for j in (await kit.peek.jobs(d.session_id)) if j.state in {"queued", "running"}]


async def test_other_edits_while_a_question_waits_do_not_resume_the_run() -> None:
    kit = make_flow_kit(ask_slot="pay.model")
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    await d.say("set design.direction=bold-dark")  # not an answer: keep waiting
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.pending_card is not None
    await d.tap("A")  # now the question is answered: the run resumes with the new look
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    assert kit.builder.calls[-1].slots["design.direction"].value == "bold-dark"


async def test_events_that_cannot_be_applied_while_planning_get_a_notice() -> None:
    d = await at_gate1()
    await d.approve_gate1()
    for event in (
        OptionSelected(event_id=d.eid(), card_instance_id="crd_x", option_ids=["A"]),
        UiAction(event_id=d.eid(), action="toggle_feature", target="chat", value=True),
        UploadAdded(
            event_id=d.eid(),
            upload_id="upl_1",
            filename="a.txt",
            media_type="text/plain",
            size_bytes=1,
        ),
    ):
        assert codes(await d.send(event)) == [messages.N_WAIT_FOR_PLAN]
    assert (await d.snapshot()).state == "PLANNING"


async def test_control_actions_in_the_wrong_state_get_a_notice() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    assert codes(await d.ui("resume")) == [messages.N_UNSUPPORTED_ACTION]  # not paused
    await d.ui("pause")
    assert codes(await d.ui("pause")) == [messages.N_UNSUPPORTED_ACTION]  # already paused
    assert codes(await d.ui("stop")) == [messages.N_NOTHING_TO_STOP]
    await d.ui("resume")
    await d.ui("archive")
    assert (await d.snapshot()).state == "ARCHIVED"
    assert codes(await d.ui("resume")) == [messages.N_ARCHIVED]


async def test_bad_targets_are_unsupported_not_errors() -> None:
    d = await at_gate1()
    assert codes(await d.ui("edit_row", "not-a-row")) == [messages.N_UNSUPPORTED_ACTION]
    assert codes(await d.ui("edit_row", "no.such_slot")) == [messages.N_UNSUPPORTED_ACTION]
    assert codes(await d.ui("edit_row")) == [messages.N_UNSUPPORTED_ACTION]
    assert codes(await d.ui("toggle_assumption", "chat", False)) == [messages.N_UNSUPPORTED_ACTION]
    assert codes(await d.ui("toggle_research_item", "RA-001", False)) == [
        messages.N_UNSUPPORTED_ACTION
    ]
    assert codes(await d.ui("undo")) == [messages.N_UNSUPPORTED_ACTION]
    assert (await d.snapshot()).state == "BRIEF_REVIEW"


async def test_edit_row_by_slot_id_works_too() -> None:
    d = await at_gate1()
    await d.ui("edit_row", "auth.method")
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.pending_card is not None
    assert snap.pending_card.card.slot == "auth.method"


async def test_handed_off_session_ignores_non_changes_and_reports_unsupported_actions() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.approve_gate2()
    assert codes(await d.say("stop")) == [messages.N_NOTHING_TO_STOP]
    out = await d.say("what is this?")  # a question: answered by the engine, nothing else happens
    assert not [e for e in out if isinstance(e, GateEvent)]
    assert codes(await d.ui("finish_with_defaults")) == [messages.N_UNSUPPORTED_ACTION]
    assert (await d.snapshot()).state == "HANDED_OFF"
    await d.ui("toggle_research_item", "RA-001", False)  # a research toggle after hand-off
    assert (await d.snapshot()).state == "PLANNING"


async def test_snapshot_reports_where_the_user_left_off() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.gate is None and snap.last_seq == 1
    assert "pause" in snap.allowed_transitions and snap.active_job is None
    await d.to_gate1()
    snap = await d.snapshot()
    assert snap.gate is not None and snap.gate.gate == "brief"
    assert snap.gate.buttons == ["Looks right", "Change something"]
    assert snap.slots["idea.archetype"].value == "commerce"
    await d.approve_gate1()
    snap = await d.snapshot()
    assert snap.active_job is not None and snap.active_job.state == "queued"
    assert snap.allowed_transitions == [
        "archive",
        "needs_answer",
        "plan_ready",
        "rediscover",
        "stop",
    ]


async def test_approver_can_read_the_session_but_a_stranger_cannot() -> None:
    kit = make_flow_kit()
    d = await kit.driver(mode="enterprise", policy_id="enterprise-default")
    await d.to_gate1()
    approver = principal("pat", roles=["client_product_owner"])
    assert (await kit.service.get_snapshot(approver, d.session_id)).state == "BRIEF_REVIEW"
    assert (await kit.service.list_versions(approver, d.session_id))[0].kind == "brief"


async def test_unknown_policy_mode_roles_are_read_from_policy_not_hardcoded() -> None:
    from mde_flow.testing.builders import enterprise_policy

    kit = make_flow_kit(
        policies=[
            enterprise_policy(
                policy_id="custom", brief_approvers=("legal",), plan_approvers=("legal", "cto")
            )
        ]
    )
    d = await kit.driver(mode="enterprise", policy_id="custom")
    gate = await d.to_gate1()
    assert gate.required_roles == ["legal"]
    legal = principal("lee", roles=["legal"])
    out = await d.decide("brief", "approve", who=legal)
    assert (await d.snapshot()).state == "PLANNING" and out


async def test_a_card_tap_at_gate_1_for_a_stale_card_just_shows_the_gate_again() -> None:
    d = await at_gate1()
    out = await d.send(
        OptionSelected(event_id=d.eid(), card_instance_id="crd_old", option_ids=["A"])
    )
    assert [e.gate for e in out if isinstance(e, GateEvent)] == ["brief"]
    assert not [e for e in out if isinstance(e, CardEvent)]


async def test_user_message_in_every_state_is_handled() -> None:
    """Smoke: a typed message never crashes whatever state the session is in."""
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("hello")
    await d.to_gate1()
    await d.say("hello again")
    await d.approve_gate1()
    await d.say("while planning")
    await d.run_job()
    await d.say("at gate 2")
    await d.approve_gate2()
    await d.say("after hand-off")
    await d.ui("pause")
    await d.say("while paused")
    await d.ui("archive")
    await d.say("after archive")
    assert (await d.snapshot()).state == "ARCHIVED"


async def test_research_toggle_in_plan_review_is_acknowledged_when_nothing_changes() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    out = await d.ui("toggle_research_item", "RA-002", True)  # already included: still a change run
    assert (await d.snapshot()).state == "PLANNING" and out


async def test_brief_row_edit_then_stop_words_in_discovering() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("pause")  # typed pause in DISCOVERING
    assert (await d.snapshot()).state == "PAUSED"
    await d.ui("resume")
    assert codes(await d.say("stop")) == [messages.N_NOTHING_TO_STOP]
    assert (await d.snapshot()).state == "DISCOVERING"


async def test_finish_with_defaults_goes_straight_to_the_brief() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.ui("finish_with_defaults")
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW"
    gate = d.last_gate("brief")
    assert gate is not None and gate.page.brief.assumptions  # type: ignore[union-attr]


async def test_message_event_ids_from_the_service_are_unique_across_sessions() -> None:
    kit = make_flow_kit()
    a = await kit.driver()
    b = await kit.driver(principal=principal("someone-else"))
    ids = [e.event_id for e in [*a.events, *b.events]]
    assert len(ids) == len(set(ids))
    assert UserMessage(event_id="x", text="y").text == "y"


async def test_budget_exhausted_during_research_skips_it_and_builds_from_defaults() -> None:
    kit = make_flow_kit()

    async def run(req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        raise BudgetExceeded("session cap")

    kit.research.run = run  # type: ignore[method-assign]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    async with kit.store.begin("tenant-a") as uow:
        saved = await uow.research.latest(d.session_id)
    assert saved is not None and saved.note.unavailable_reason == "budget"
    assert messages.N_RESEARCH_UNAVAILABLE in codes(d.events)


async def test_budget_exhausted_before_the_build_pauses_with_an_explanation() -> None:
    kit = make_flow_kit()

    async def build(req: BuildRequest, ctx: RunContext) -> BuildResult:
        raise BudgetExceeded("session cap")

    kit.builder.build = build  # type: ignore[method-assign]
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).state == "BRIEF_REVIEW"
    assert messages.N_BUDGET_REACHED in codes(d.events)
    assert messages.N_BUILD_FAILED not in codes(d.events)
