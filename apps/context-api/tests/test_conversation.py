"""A whole conversation through the HTTP API: hi -> Gate 1 -> plan job -> Gate 2 -> spec_ready."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mde_context_api.composition import AppState
from mde_context_api.testing import Api
from mde_contracts.events import (
    AssistantMessage,
    GateEvent,
    Notice,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
)
from mde_core.hashing import sha256_hex


def git_head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def project_dir(state: AppState, tenant: str, app_id: str) -> Path:
    return state.settings.projects_dir / tenant / app_id


async def test_hi_to_spec_ready_through_both_gates(api: Api, state: AppState) -> None:
    data = await api.create()
    app_id = data["session"]["app_id"]

    # "hi" is answered from the canned router (no card yet), then a starter chip starts the idea
    await api.say("hi")
    gate1 = await api.to_gate1()
    assert gate1.gate == "brief" and not gate1.single_button
    assert gate1.buttons == ["Looks right", "Change something"]

    brief = await api.get("/brief")
    assert brief.status_code == 200
    assert brief.json()["hash"] == gate1.hash and brief.json()["version"] == gate1.version

    # Gate 1 approved: 202, the run is queued, nothing has been researched or built yet
    resp = await api.approve("brief")
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "PLANNING" and body["plan_phase"] == "research"
    assert body["job"]["state"] == "queued"
    assert any(isinstance(e, StateChanged) and e.to_state == "PLANNING" for e in api.events)
    assert (await api.get("/spec")).status_code == 404
    assert not project_dir(state, "tenant-a", app_id).exists()

    # the worker runs the job (inline here): progress, plan_ready, Gate 2
    assert await state.flow.run_next_job()
    await api.refresh()
    assert any(isinstance(e, ProgressEvent) for e in api.events)
    [plan_ready] = api.of_type(PlanReady)
    gate2 = api.last_gate("plan")
    assert gate2 is not None and gate2.hash == plan_ready.hash
    snap = await api.snapshot()
    assert snap["state"] == "PLAN_REVIEW" and snap["active_job"] is None
    assert not project_dir(state, "tenant-a", app_id).exists()  # nothing committed before Gate 2

    plan = await api.get("/plan")
    assert plan.status_code == 200 and plan.json()["spec_hash"] == gate2.hash

    # Gate 2 approved: spec_ready, the file is committed
    resp = await api.approve("plan")
    assert resp.status_code == 200 and resp.json()["state"] == "HANDED_OFF"
    [ready] = api.of_type(SpecReady)
    assert ready.hash == gate2.hash and ready.version == gate2.version and ready.app_id == app_id

    spec = await api.get("/spec")
    assert spec.status_code == 200
    assert spec.headers["content-type"].startswith("text/markdown")
    assert spec.headers["x-spec-hash"] == ready.hash == sha256_hex(spec.text)
    assert spec.headers["x-spec-version"] == str(ready.version)
    assert spec.headers["x-spec-status"] == "handed_off"
    assert spec.headers["x-spec-git-sha"] == ready.git_sha
    assert spec.headers["etag"] == f'"{ready.hash}"'

    repo = project_dir(state, "tenant-a", app_id)
    committed = (repo / "APP_SPEC.md").read_bytes()
    assert sha256_hex(committed.decode("utf-8")) == ready.hash  # byte for byte
    assert committed == spec.content
    head = git_head(repo)
    assert head == ready.git_sha

    versions = (await api.get("/versions")).json()["versions"]
    kinds = [(v["kind"], v["status"]) for v in versions]
    assert ("brief", "approved") in kinds and ("spec", "handed_off") in kinds
    assert (await api.snapshot())["handed_off_version"] == ready.version


async def test_a_duplicate_event_id_returns_the_same_replies_with_no_second_effect(
    api: Api,
) -> None:
    await api.create()
    greeting = api.events[0]
    assert isinstance(greeting, AssistantMessage)
    await api.say(greeting.chips[0])
    card = (await api.snapshot())["pending_card"]
    tap = {
        "type": "option_selected",
        "event_id": "evt-tap-once",
        "card_instance_id": card["instance_id"],
        "option_ids": [card["card"]["options"][0]["id"]],
    }
    first = await api.post(tap)
    assert first.status_code == 200
    stored_before = await api.refresh()
    second = await api.post(tap)
    assert second.status_code == 200
    assert second.json()["events"] == first.json()["events"]  # same ids, same seqs
    assert await api.refresh() == stored_before  # nothing new was appended
    third = await api.post(tap)
    assert third.json()["events"] == first.json()["events"]


async def test_approving_twice_with_one_event_id_starts_one_run(api: Api, state: AppState) -> None:
    await api.create()
    gate1 = await api.to_gate1()
    decision = {
        "type": "gate_decision",
        "event_id": "evt-approve-once",
        "gate": "brief",
        "decision": "approve",
        "version": gate1.version,
        "hash": gate1.hash,
    }
    first = await api.post(decision)
    second = await api.post(decision)
    assert first.status_code == 202
    assert second.json()["events"] == first.json()["events"]
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert await state.flow.run_next_job() and not await state.flow.run_next_job()  # one job only


async def test_a_stale_hash_is_not_counted_and_the_gate_is_shown_again(api: Api) -> None:
    await api.create()
    gate1 = await api.to_gate1()
    resp = await api.decide("brief", version=gate1.version, hash="0" * 64)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "BRIEF_REVIEW" and body["job"] is None
    types = [(e["type"], e.get("code")) for e in body["events"]]
    assert ("notice", "stale_gate") in types
    assert any(e["type"] == "gate" and e["hash"] == gate1.hash for e in body["events"])
    assert (await api.snapshot())["state"] == "BRIEF_REVIEW"  # the real hash still approves
    ok = await api.approve("brief")
    assert ok.status_code == 202


async def test_a_stale_version_is_not_counted_either(api: Api) -> None:
    await api.create()
    gate1 = await api.to_gate1()
    resp = await api.decide("brief", version=gate1.version + 7, hash=gate1.hash)
    assert resp.status_code == 200 and resp.json()["state"] == "BRIEF_REVIEW"
    assert any(e.get("code") == "stale_gate" for e in resp.json()["events"])


async def test_a_decision_for_a_gate_that_is_not_open_is_a_friendly_notice(api: Api) -> None:
    await api.create()
    resp = await api.decide("plan", version=1, hash="a" * 64)
    assert resp.status_code == 200
    [notice] = [e for e in resp.json()["events"] if e["type"] == "notice"]
    assert notice["code"] == "gate_not_open"
    assert resp.json()["state"] == "DISCOVERING"


async def test_change_something_at_gate_1_reopens_the_card_then_returns_with_one_button(
    api: Api,
) -> None:
    await api.create()
    gate1 = await api.to_gate1()
    await api.ui("edit_row", "look")
    snap = await api.snapshot()
    assert snap["state"] == "DISCOVERING" and snap["pending_card"]["card"]["slot"] == (
        "design.direction"
    )
    await api.tap_recommended()
    gate = api.last_gate("brief")
    assert gate is not None and gate.version == gate1.version + 1
    assert gate.single_button and gate.diff_lines  # a re-opened gate: one button and a diff
    assert (await api.snapshot())["state"] == "BRIEF_REVIEW"


async def test_stop_during_planning_returns_to_the_gate_and_cancels_the_job(
    api: Api, state: AppState
) -> None:
    await api.create()
    await api.to_gate1()
    resp = await api.approve("brief")
    assert resp.json()["state"] == "PLANNING"
    stopped = await api.ui("stop")
    assert any(isinstance(e, StateChanged) and e.to_state == "BRIEF_REVIEW" for e in stopped)
    assert any(isinstance(e, GateEvent) and e.gate == "brief" for e in stopped)  # shown again
    snap = await api.snapshot()
    assert snap["state"] == "BRIEF_REVIEW" and snap["active_job"] is None
    assert not await state.flow.run_next_job()  # the cancelled job is never claimed
    # and the user can simply approve again
    again = await api.decide("brief", version=snap["gate"]["version"], hash=snap["gate"]["hash"])
    assert again.status_code == 202


async def test_typing_during_planning_is_queued_not_lost(api: Api, state: AppState) -> None:
    await api.create()
    await api.to_gate1()
    await api.approve("brief")
    out = await api.say("could the buttons be bigger?")
    assert any(isinstance(e, (Notice, AssistantMessage)) for e in out)
    assert (await api.snapshot())["state"] == "PLANNING"
    assert await state.flow.run_next_job()
    assert (await api.snapshot())["state"] == "PLAN_REVIEW"


@pytest.mark.parametrize("text", ["yes", "go ahead", "build it"])
async def test_a_typed_yes_approves_when_the_gate_is_open_and_records_that_it_was_typed(
    api: Api, state: AppState, text: str
) -> None:
    await api.create()
    await api.to_gate1()
    resp = await api.post({"type": "user_message", "event_id": api.eid(), "text": text})
    assert resp.status_code == 202, resp.text
    assert resp.json()["state"] == "PLANNING"
    async with state.store.begin("tenant-a") as uow:
        [approval] = await uow.approvals.list(api.session_id, "brief")
    assert approval.via == "typed" and approval.decision == "approve"


async def test_a_typed_yes_with_no_gate_open_never_approves_anything(
    api: Api, state: AppState
) -> None:
    await api.create()
    greeting = api.events[0]
    assert isinstance(greeting, AssistantMessage)
    await api.say(greeting.chips[0])
    assert (await api.snapshot())["state"] == "DISCOVERING"
    resp = await api.post({"type": "user_message", "event_id": api.eid(), "text": "yes"})
    assert resp.status_code == 200 and resp.json()["state"] != "PLANNING"
    async with state.store.begin("tenant-a") as uow:
        assert await uow.approvals.list(api.session_id, "brief") == []
        assert await uow.jobs.active_for_session(api.session_id) is None


async def test_an_unclear_typed_approval_is_not_an_approval(api: Api) -> None:
    await api.create()
    await api.to_gate1()
    resp = await api.post({"type": "user_message", "event_id": api.eid(), "text": "ok maybe"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "BRIEF_REVIEW" and resp.json()["job"] is None
    assert any(e["type"] == "gate" for e in resp.json()["events"])  # the buttons again


async def test_a_gate_tap_is_recorded_as_a_tap(api: Api, state: AppState) -> None:
    await api.create()
    await api.to_gate1()
    await api.approve("brief")
    async with state.store.begin("tenant-a") as uow:
        [approval] = await uow.approvals.list(api.session_id, "brief")
    assert approval.via == "tap" and approval.approver_id == "user-1"


async def test_a_change_at_gate_2_makes_a_new_version_and_the_old_hash_is_stale(
    api: Api, state: AppState
) -> None:
    await api.create()
    await api.to_gate1()
    await api.approve("brief")
    await state.flow.run_next_job()
    await api.refresh()
    gate2 = api.last_gate("plan")
    assert gate2 is not None

    await api.say("make it bold and dark")  # a change is its own approval of that change
    snap = await api.snapshot()
    assert snap["state"] == "PLANNING" and snap["active_job"]["state"] == "queued"
    assert await state.flow.run_next_job()
    await api.refresh()
    gate = api.last_gate("plan")
    assert gate is not None and gate.version == gate2.version + 1 and gate.hash != gate2.hash
    assert gate.single_button and gate.diff_lines == ["Updated the look"]

    stale = await api.decide("plan", version=gate2.version, hash=gate2.hash)
    assert any(e.get("code") == "stale_gate" for e in stale.json()["events"])
    assert (await api.snapshot())["state"] == "PLAN_REVIEW"
    done = await api.decide("plan", version=gate.version, hash=gate.hash)
    assert done.json()["state"] == "HANDED_OFF"


async def test_silence_nudges_once_then_assumes_the_recommended_option(api: Api) -> None:
    """The client reports ``idle``: one nudge, then the recommended answer is assumed."""
    await api.create()
    greeting = api.events[0]
    assert isinstance(greeting, AssistantMessage)
    await api.say(greeting.chips[0])
    snap = await api.snapshot()
    for _ in range(8):  # find a card that is not just a pre-filled confirmation
        if snap["pending_card"]["card"]["slot"] not in snap["slots"]:
            break
        await api.tap_recommended()
        snap = await api.snapshot()
    first = snap["pending_card"]
    slot = first["card"]["slot"]
    assert slot not in snap["slots"]

    nudge = await api.ui("idle")
    assert [e.type for e in nudge] == ["assistant_message", "card"]
    assert isinstance(nudge[0], AssistantMessage) and "Take your time" in nudge[0].text
    snap = await api.snapshot()
    assert snap["pending_card"]["instance_id"] == first["instance_id"]  # the same card again
    assert slot not in snap["slots"]  # nothing was assumed by the nudge

    await api.ui("idle")  # silence again
    snap = await api.snapshot()
    assert snap["slots"][slot]["state"] == "assumed"
    assert (
        snap["pending_card"] is None or snap["pending_card"]["instance_id"] != first["instance_id"]
    )


async def test_silence_never_approves_a_gate(api: Api) -> None:
    await api.create()
    await api.to_gate1()
    for _ in range(3):
        resp = await api.post({"type": "ui_action", "event_id": api.eid(), "action": "idle"})
        assert resp.status_code == 200 and resp.json()["events"] == []
    snap = await api.snapshot()
    assert snap["state"] == "BRIEF_REVIEW" and snap["active_job"] is None
