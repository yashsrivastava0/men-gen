"""The D-048 integration items on the flow side: silence (idle), what Research and the builder are
told (references, brand site, change requests, the first draft) and gate comments.

Run over the in-memory fake and over Postgres (``store_matrix``)."""

from __future__ import annotations

import pytest

from mde_contracts.cards import Card, CardInstance, CardOption, CardProgress
from mde_contracts.engines import DiscoveryRequest, DiscoveryResult, DiscoveryState
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    InboundEvent,
    UiAction,
    UserMessage,
)
from mde_contracts.records import SessionRecord
from mde_contracts.slots import SlotRecord
from mde_flow.testing.builders import enterprise_policy
from mde_flow.testing.fakes import FAKE_IDLE_NUDGE
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


async def session_record(kit: FlowKit, d: Driver) -> SessionRecord:
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    return rec


async def amend_discovery(kit: FlowKit, d: Driver, **changes: object) -> None:
    """Change the stored Discover state (as an engine would have left it) between turns."""
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id, for_update=True)
        assert rec is not None
        state = rec.discovery.model_copy(update=changes)
        await uow.sessions.save(
            rec.model_copy(update={"discovery": state}), expected_version=rec.version
        )


def put_slot(
    slots: dict[str, SlotRecord], slot_id: str, value: object, *, state: str, source: str
) -> None:
    slots[slot_id] = SlotRecord.model_validate(
        {"slot_id": slot_id, "value": value, "state": state, "source": source}
    )


# =========================================================================== idle: silence
#
# The flow hands ``idle`` to Discover in DISCOVERING and does nothing anywhere else; the nudge and
# the assumption are Discover's (the scripted engine below keeps the real engine's contract).


async def test_the_first_silence_nudges_once_and_shows_the_same_card_again() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("a fitness tracking app")
    card = (await d.snapshot()).pending_card
    assert card is not None
    calls = list(kit.discovery.calls)

    out = await d.ui("idle")
    assert kit.discovery.calls[-1] == ("handle", "ui_action:idle")  # the engine got the event
    assert [type(e) for e in out] == [AssistantMessage, CardEvent]
    assert isinstance(out[0], AssistantMessage) and out[0].text == FAKE_IDLE_NUDGE
    assert isinstance(out[1], CardEvent) and out[1].card.instance_id == card.instance_id
    assert kit.discovery.calls == [*calls, ("handle", "ui_action:idle")]  # one engine turn, no more
    rec = await session_record(kit, d)
    assert rec.discovery.nudged_card_ids == [card.card.id]
    assert rec.discovery.pending_card is not None and rec.state == "DISCOVERING"
    assert rec.discovery.slots.get(card.card.slot) is None  # nothing was assumed yet


async def test_silence_after_the_nudge_assumes_the_recommended_option() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("a fitness tracking app")
    card = (await d.snapshot()).pending_card
    assert card is not None
    recommended = next(o for o in card.card.options if o.recommended)
    await d.ui("idle")  # the nudge
    out = await d.ui("idle")  # silence again

    # the engine was handed the second silence itself and took the recommendation on that card
    assert kit.discovery.calls[-2:] == [("handle", "ui_action:idle")] * 2
    rec = await session_record(kit, d)
    assert rec.discovery.slots[card.card.slot].state == "assumed"
    assert rec.discovery.slots[card.card.slot].value == recommended.sets[card.card.slot]
    assert rec.discovery.pending_card is None or rec.discovery.pending_card.instance_id != (
        card.instance_id
    )
    assert any(isinstance(e, CardEvent) for e in out)  # and on to the next question


async def test_each_card_gets_its_own_nudge() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("a fitness tracking app")
    first = (await d.snapshot()).pending_card
    assert first is not None
    await d.ui("idle")
    await d.ui("idle")  # assumed: the next card appears
    second = (await d.snapshot()).pending_card
    assert second is not None and second.card.id != first.card.id
    out = await d.ui("idle")  # silence on the new card: a nudge, not an assumption
    assert isinstance(out[0], AssistantMessage) and out[0].text == FAKE_IDLE_NUDGE
    rec = await session_record(kit, d)
    assert rec.discovery.nudged_card_ids == [first.card.id, second.card.id]
    assert rec.discovery.slots.get(second.card.slot) is None


async def test_a_repeated_idle_event_id_nudges_only_once() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("a fitness tracking app")
    event = UiAction(event_id="evt-idle-1", action="idle")
    first = await d.send(event)
    again = await d.send(event)
    assert [e.event_id for e in again] == [e.event_id for e in first]
    rec = await session_record(kit, d)
    assert len(rec.discovery.nudged_card_ids) == 1
    assert rec.discovery.slots.get("design.direction") is None


async def test_silence_with_nothing_pending_does_nothing() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    assert await d.ui("idle") == []  # a fresh session: the greeting asks nothing
    assert kit.discovery.calls == [("start", ""), ("handle", "ui_action:idle")]
    assert (await session_record(kit, d)).discovery.nudged_card_ids == []


async def test_silence_never_approves_and_says_nothing_at_a_gate() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    calls = list(kit.discovery.calls)
    for _ in range(3):
        assert await d.ui("idle") == []
    assert kit.discovery.calls == calls  # silence at a gate never reaches the engine
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW" and snap.active_job is None
    assert await kit.peek.approvals(d.session_id) == []

    await d.approve_gate1()
    assert await d.ui("idle") == []  # PLANNING: no notice either
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    for _ in range(3):
        assert await d.ui("idle") == []
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    await d.approve_gate2()
    assert await d.ui("idle") == []
    assert (await d.snapshot()).state == "HANDED_OFF"
    assert len(kit.fake_repo.commits) == 1  # only the real approval handed anything off
    assert ("handle", "ui_action:idle") not in kit.discovery.calls  # no engine turn at all


async def test_silence_while_paused_or_archived_changes_nothing() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.say("a fitness tracking app")
    await d.ui("pause")
    assert await d.ui("idle") == []
    assert (await d.snapshot()).state == "PAUSED"
    await d.ui("resume")
    await d.ui("archive")
    assert await d.ui("idle") == []
    assert (await d.snapshot()).state == "ARCHIVED"
    assert ("handle", "ui_action:idle") not in kit.discovery.calls


def _help_card_state(req: DiscoveryRequest) -> DiscoveryState:
    card = Card(
        id="Q-help-x",
        slot="idea.summary",
        type="choice3",
        text="Here are some ideas",
        options=[
            CardOption(id="A", label="A shop", recommended=True, sets={"idea.summary": "shop"})
        ],
        kind="help",
    )
    instance = CardInstance(
        instance_id="crd_help", card=card, progress=CardProgress(asked=1, cap=10)
    )
    return req.state.model_copy(update={"pending_card": instance})


async def test_a_help_card_is_nudged_once_and_then_left_alone() -> None:
    kit = make_flow_kit()
    d = await kit.driver()

    def show_help(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UserMessage) and event.text == "help me":
            return DiscoveryResult(state=_help_card_state(req), intent="confused")
        return None

    kit.discovery.override = show_help
    await d.say("help me")
    assert (await d.snapshot()).pending_card is not None
    nudge = await d.ui("idle")
    assert isinstance(nudge[0], AssistantMessage)
    assert await d.ui("idle") == []  # a help card is never assumed
    assert await d.ui("idle") == []
    assert (await d.snapshot()).pending_card is not None
    assert (await session_record(kit, d)).discovery.slots.get("idea.summary") is None


async def test_silence_on_a_builder_question_assumes_the_answer_and_resumes_the_run() -> None:
    kit = make_flow_kit(ask_slot="pay.model")
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.pending_card is not None
    assert snap.pending_card.card.slot == "pay.model"
    await d.ui("idle")  # the nudge
    await d.ui("idle")  # silence again: assume, which answers the builder's question
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "build"
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert "pay.model" in kit.builder.calls[-1].answer_slots
    rec = await session_record(kit, d)
    assert rec.discovery.slots["pay.model"].state == "assumed"


# =========================================================================== research inputs


async def test_research_is_told_the_named_apps_and_the_brand_site_the_user_gave() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    slots = dict(rec.discovery.slots)
    put_slot(
        slots,
        "brand.assets",
        "our site is https://acme.example/brand.",
        state="volunteered",
        source="text",
    )
    await amend_discovery(kit, d, slots=slots, references=["Zomato", "zomato", " Swiggy "])
    await d.approve_gate1()
    await d.run_job()
    [request] = kit.research.calls
    assert request.references == ["Zomato", "Swiggy"]
    assert request.brand_url == "https://acme.example/brand"


async def test_research_is_never_given_a_guessed_or_untrusted_brand_site() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    slots = dict(rec.discovery.slots)
    put_slot(slots, "brand.assets", "https://acme.example", state="assumed", source="default")
    put_slot(slots, "brand.name", "https://evil.example", state="volunteered", source="upload")
    await amend_discovery(kit, d, slots=slots)
    await d.approve_gate1()
    await d.run_job()
    [request] = kit.research.calls
    assert request.brand_url is None and request.references == []


async def test_a_site_in_the_brand_name_card_is_used_when_the_user_typed_it() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    slots = dict(rec.discovery.slots)
    put_slot(slots, "brand.name", "www.acme.example", state="confirmed", source="tap")
    await amend_discovery(kit, d, slots=slots)
    await d.approve_gate1()
    await d.run_job()
    assert kit.research.calls[0].brand_url == "https://www.acme.example"


# =========================================================================== build inputs


async def test_the_builder_gets_the_change_requests_of_the_run_and_the_first_draft() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    first = kit.builder.calls[0]
    assert first.change_ops == [] and first.gate1_spec is None  # nothing is built yet

    await d.say("set design.direction=bold-dark")
    await d.run_job()
    second = kit.builder.calls[1]
    assert [(o.kind, o.slot_id) for o in second.change_ops] == [("set_slot", "design.direction")]
    assert second.gate1_spec is not None
    draft = await kit.service.get_spec(d.who, d.session_id, 1)
    assert draft is not None and second.gate1_spec == draft.spec  # the first draft itself
    assert second.gate1_spec_ids == sorted(second.gate1_spec_ids) and second.gate1_spec_ids

    await d.say("set design.direction=warm-friendly")  # a later change still compares to draft 1
    await d.run_job()
    third = kit.builder.calls[2]
    assert third.gate1_spec == draft.spec
    assert [o.value_text for o in third.change_ops] == ["warm-friendly"]


async def test_change_requests_made_by_taps_reach_the_builder_too() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    await d.ui("toggle_feature", "chat", True)
    await d.run_job()
    ops = kit.builder.calls[-1].change_ops
    assert [(o.kind, o.target_id, o.value_bool) for o in ops] == [("toggle_feature", "chat", True)]


# =========================================================================== gate comments


def capture_sources(kit: FlowKit) -> list[tuple[str, str]]:
    """Wrap the fake discovery so every ``handle`` call records (text, request source)."""
    seen: list[tuple[str, str]] = []

    def spy(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if isinstance(event, UserMessage):
            seen.append((event.text, req.source))
        return None

    kit.discovery.override = spy
    return seen


async def test_a_comment_on_a_change_something_decision_is_a_gate_comment() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    seen = capture_sources(kit)
    await d.decide("brief", "change", comment="set design.direction=bold-dark")
    assert seen == [("set design.direction=bold-dark", "gate_comment")]
    assert (await d.snapshot()).state == "BRIEF_REVIEW"  # applied: a new Brief version


async def test_every_line_of_a_rejection_is_a_gate_comment() -> None:
    kit = make_flow_kit(
        policies=[enterprise_policy(policy_id="two-step", plan_approvers=("requester",))]
    )
    d = await kit.driver(mode="enterprise", policy_id="two-step")
    await d.to_gate2()
    seen = capture_sources(kit)
    await d.decide("plan", "reject", comment="set design.direction=bold-dark\nfeature+ chat")
    assert seen == [
        ("set design.direction=bold-dark", "gate_comment"),
        ("feature+ chat", "gate_comment"),
    ]


async def test_text_typed_at_a_gate_or_queued_during_planning_is_plain_text() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    seen = capture_sources(kit)
    await d.say("set design.direction=bold-dark")  # typed at the gate: a message, not a comment
    assert seen == [("set design.direction=bold-dark", "text")]
    await d.approve_gate1()
    await d.say("could the buttons be bigger?")  # during PLANNING: queued, replayed later as text
    seen.clear()
    await d.run_job()
    assert seen == [("could the buttons be bigger?", "text")]
