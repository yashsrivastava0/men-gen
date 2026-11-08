"""Tenant isolation and who may act: tenant B cannot read, stream, post to or even detect tenant A's
sessions; inside a tenant only the owner and the required approvers may act (Enterprise roles)."""

from __future__ import annotations

from mde_context_api.composition import AppState
from mde_context_api.testing import Api
from mde_contracts.events import SpecReady


async def finished_session(api: Api, state: AppState) -> None:
    """A session with every document: Brief, plan, spec, handed off."""
    await api.create()
    await api.to_gate1()
    await api.approve("brief")
    await state.flow.run_next_job()
    await api.refresh()
    await api.approve("plan")


READ_TAILS = ["", "/brief", "/plan", "/spec", "/versions"]


async def test_tenant_b_cannot_read_anything_of_tenant_a(api: Api, state: AppState) -> None:
    await finished_session(api, state)
    for tail in READ_TAILS:
        mine = await api.get(tail)
        assert mine.status_code == 200, tail

    stranger = api.as_user("user-1", tenant="tenant-b")  # even the same user id
    for tail in [*READ_TAILS, "/stream"]:
        theirs = await stranger.get(tail)
        assert theirs.status_code == 404, tail
        assert theirs.json()["code"] == "not_found"
    # nothing says "that exists but is not yours": the answer for another tenant's id is the same
    # as for an id that never existed
    unknown = api.as_user("user-1", tenant="tenant-b")
    unknown.session_id = "ses_01UNKNOWN0000000000000000"
    for tail in READ_TAILS:
        a = (await stranger.get(tail)).json()
        b = (await unknown.get(tail)).json()
        assert a["code"] == b["code"] == "not_found"
        assert a["message"].replace(api.session_id, "X") == b["message"].replace(
            unknown.session_id, "X"
        )


async def test_tenant_b_cannot_post_events_to_tenant_a(api: Api, state: AppState) -> None:
    await api.create()
    gate1 = await api.to_gate1()
    before = await api.refresh()
    stranger = api.as_user("user-1", tenant="tenant-b")
    for event in (
        {"type": "user_message", "event_id": "evt-x1", "text": "yes"},
        {"type": "ui_action", "event_id": "evt-x2", "action": "restart"},
        {
            "type": "gate_decision",
            "event_id": "evt-x3",
            "gate": "brief",
            "decision": "approve",
            "version": gate1.version,
            "hash": gate1.hash,
        },
    ):
        resp = await stranger.post(event)
        assert resp.status_code == 404, event["type"]
        assert resp.json()["code"] == "not_found"
    assert await api.refresh() == before  # no effect on tenant A
    assert (await api.snapshot())["state"] == "BRIEF_REVIEW"
    async with state.store.begin("tenant-a") as uow:
        assert await uow.jobs.active_for_session(api.session_id) is None


async def test_the_same_event_id_in_two_tenants_does_not_collide(api: Api) -> None:
    await api.create()
    other = Api(api.client, tenant="tenant-b", user="user-1")
    await other.create()
    event = {"type": "user_message", "event_id": "evt-shared", "text": "hi"}
    first = await api.post(event)
    second = await other.post(event)
    assert first.status_code == second.status_code == 200
    assert {e["session_id"] for e in first.json()["events"]} == {api.session_id}
    assert {e["session_id"] for e in second.json()["events"]} == {other.session_id}


async def test_each_tenant_only_ever_sees_its_own_events(api: Api) -> None:
    await api.create()
    other = Api(api.client, tenant="tenant-b", user="user-1")
    await other.create()
    await api.say("hi")
    mine = await api.refresh()
    theirs = await other.refresh()
    assert {e.session_id for e in mine} == {api.session_id}
    assert {e.session_id for e in theirs} == {other.session_id}
    assert len(theirs) == 1  # tenant B's session got none of tenant A's events


async def test_a_user_with_no_role_cannot_find_the_session_inside_the_tenant(api: Api) -> None:
    """Same tenant, no role in the session's gates: it is simply not there (no probing), in the
    same words as an id that never existed."""
    await api.create()
    carol = api.as_user("carol")
    unknown = api.as_user("carol")
    unknown.session_id = "ses_01UNKNOWN0000000000000000"
    for tail in ("", "/brief", "/plan", "/spec", "/versions", "/stream"):
        read = await carol.get(tail)
        gone = await unknown.get(tail)
        assert read.status_code == 404 and read.json()["code"] == "not_found", tail
        assert read.json()["message"].replace(api.session_id, "X") == gone.json()[
            "message"
        ].replace(unknown.session_id, "X")
    write = await carol.post({"type": "user_message", "event_id": "evt-c1", "text": "hi"})
    assert write.status_code == 404


async def test_a_claimed_requester_role_is_not_the_requester(api: Api, state: AppState) -> None:
    """``X-Roles: requester`` from anyone but the owner grants nothing: not a read, not a decision."""
    await api.create()
    gate1 = await api.to_gate1()
    mallory = api.as_user("mallory", ["requester"])
    assert (await mallory.get()).status_code == 404
    approve = await mallory.post(
        {
            "type": "gate_decision",
            "event_id": "evt-m1",
            "gate": "brief",
            "decision": "approve",
            "version": gate1.version,
            "hash": gate1.hash,
        }
    )
    assert approve.status_code == 404 and approve.json()["code"] == "not_found"
    assert (await api.snapshot())["state"] == "BRIEF_REVIEW"
    async with state.store.begin("tenant-a") as uow:
        assert await uow.approvals.list(api.session_id, "brief") == []
    owner = api.as_user("user-1", ["requester"])  # the owner is unaffected
    assert (await owner.get()).status_code == 200


async def test_enterprise_needs_every_required_approver_on_the_same_hash(
    api: Api, state: AppState
) -> None:
    await api.create("enterprise")
    await api.to_gate1(limit=40)
    assert api.last_gate("brief") is not None and api.last_gate("brief").required_roles == [  # type: ignore[union-attr]
        "requester"
    ]
    await api.approve("brief")
    await state.flow.run_next_job()
    await api.refresh()
    gate2 = api.last_gate("plan")
    assert gate2 is not None and gate2.required_roles == ["client_product_owner", "delivery_lead"]
    assert gate2.buttons[0] == "Send for approval"

    # the requester's own press only sends it for approval
    sent = await api.decide("plan")
    assert any(e.get("code") == "submitted_for_approval" for e in sent.json()["events"])
    assert sent.json()["state"] == "PLAN_REVIEW"

    # an approver for one role: still waiting for the other
    alice = api.as_user("alice", ["client_product_owner"])
    assert (await alice.get()).status_code == 200  # approvers may read
    first = await alice.decide("plan")
    assert first.status_code == 200 and first.json()["state"] == "PLAN_REVIEW"
    assert any(
        e.get("code") == "waiting_for_approvers" and "delivery_lead" in e["text"]
        for e in first.json()["events"]
    )
    assert not (state.settings.projects_dir / "tenant-a").exists()  # nothing committed yet

    # a role that is not required cannot decide; a role you do not hold cannot be claimed
    intern = api.as_user("ivy", ["intern"])
    assert (await intern.decide("plan")).status_code == 404  # no role here: not even visible
    mismatch = await alice.post(
        {
            "type": "gate_decision",
            "event_id": "evt-claim",
            "gate": "plan",
            "decision": "approve",
            "version": gate2.version,
            "hash": gate2.hash,
            "role": "delivery_lead",
        }
    )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "gate_error"
    assert "decision" in mismatch.json()["message"]  # friendly text, no internals
    spoof = await alice.post(
        {
            "type": "gate_decision",
            "event_id": "evt-spoof",
            "gate": "plan",
            "decision": "approve",
            "version": gate2.version,
            "hash": gate2.hash,
            "approver_id": "bob",
        }
    )
    assert spoof.status_code == 409 and spoof.json()["code"] == "gate_error"
    assert (await api.snapshot())["state"] == "PLAN_REVIEW"

    # the second required approver completes it: spec_ready only now
    bob = api.as_user("bob", ["delivery_lead"])
    done = await bob.decide("plan")
    assert done.json()["state"] == "HANDED_OFF"
    [ready] = [e for e in done.json()["events"] if e["type"] == "spec_ready"]
    assert ready["hash"] == gate2.hash
    assert SpecReady.model_validate(ready).version == gate2.version
    assert (await api.get("/spec")).headers["x-spec-status"] == "handed_off"
