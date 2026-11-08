"""``Conversation``: the log of events, the five inbound events, resume, and waiting for the plan."""

from __future__ import annotations

import pytest

from mde_cli.client import ApiClient, Identity
from mde_cli.conversation import (
    Conversation,
    ConversationError,
    PlanTimeout,
    recommended_ids,
    resolve_options,
)
from mde_context_api.composition import AppState
from mde_contracts.cards import Card, CardOption
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateEvent,
    Notice,
    OutboundEvent,
    ProgressEvent,
    SpecReady,
)


def message(seq: int) -> AssistantMessage:
    return AssistantMessage(event_id=f"evt_{seq}", session_id="ses_1", seq=seq, text=f"m{seq}")


def card(kind: str = "choice3") -> Card:
    return Card(
        id="Q-1",
        slot="idea.archetype",
        type=kind,  # type: ignore[arg-type]
        text="?",
        options=[
            CardOption(id="A", label="An online shop"),
            CardOption(id="B", label="Bookings", recommended=True),
            CardOption(id="C", label="Something else", recommended=kind == "multi"),
        ],
    )


# --------------------------------------------------------------------------- option helpers


def test_options_resolve_by_id_or_label_and_an_unknown_pick_names_the_choices() -> None:
    assert resolve_options(card(), ["a", "bookings"]) == ["A", "B"]
    with pytest.raises(ConversationError, match="options: A, B, C"):
        resolve_options(card(), ["nope"])


def test_the_recommended_ids() -> None:
    assert recommended_ids(card()) == ["B"]
    assert recommended_ids(card("multi")) == ["B", "C"]
    assert recommended_ids(card("text")) == []
    empty = Card(id="Q-2", slot="idea.archetype", type="choice3", text="?")
    assert recommended_ids(empty) == []
    first = Card(
        id="Q-3",
        slot="idea.archetype",
        type="choice3",
        text="?",
        options=[CardOption(id="X", label="x"), CardOption(id="Y", label="y")],
    )
    assert recommended_ids(first) == ["X"]  # nothing suggested: the first option


# --------------------------------------------------------------------------- the log


async def test_each_event_is_handed_out_once_and_the_cursor_skips_over_a_gap(
    api: ApiClient,
) -> None:
    shown: list[int] = []
    conv = Conversation(api, on_event=lambda e: shown.append(e.seq))
    assert [e.seq for e in conv._accept([message(1), message(3)])] == [1, 3]  # pyright: ignore[reportPrivateUsage]
    assert conv.contiguous == 1  # 2 is missing: a fetch must start after 1, not after 3
    assert conv._accept([message(1), message(3)]) == []  # pyright: ignore[reportPrivateUsage]
    new = conv._accept([message(2), message(3), message(4)])  # pyright: ignore[reportPrivateUsage]
    assert [e.seq for e in new] == [2, 4]
    assert conv.contiguous == 4 and shown == [1, 3, 2, 4]


async def test_start_keeps_the_greeting_and_the_owner(api: ApiClient) -> None:
    conv = Conversation(api)
    events = await conv.start("guided")
    assert conv.session_id.startswith("ses_") and conv.state == "DISCOVERING"
    assert events and isinstance(events[0], AssistantMessage) and events[0].canned
    assert conv.owner == api.identity and conv.last_gate is None


async def test_a_conversation_runs_from_a_message_to_the_brief_gate(api: ApiClient) -> None:
    seen: list[OutboundEvent] = []
    conv = Conversation(api, on_event=seen.append)
    await conv.start("guided")
    await conv.say("An online shop for handmade candles")
    assert conv.snap is not None and conv.snap.pending_card is not None
    first_card = conv.snap.pending_card
    taken = await conv.tap_recommended()
    assert any(isinstance(e, CardEvent) for e in taken) or conv.state != "DISCOVERING"
    await conv.answer_with_recommended()
    assert conv.state == "BRIEF_REVIEW"
    gate = conv.last_gate
    assert gate is not None and gate.gate == "brief" and conv.gate_events("plan") == []
    assert [e.seq for e in seen] == sorted({e.seq for e in seen})  # each once, in order
    assert first_card.instance_id != (
        conv.snap.pending_card.instance_id if conv.snap.pending_card else ""
    )


async def test_taps_need_a_pending_card_and_decisions_need_an_open_gate(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    with pytest.raises(ConversationError, match="no question"):
        await conv.tap(["A"])
    with pytest.raises(ConversationError, match="no question"):
        await conv.tap_recommended()
    with pytest.raises(ConversationError, match="no page waiting"):
        await conv.decide("approve")
    await conv.say("An online shop for handmade candles")
    with pytest.raises(ConversationError, match="no page waiting"):
        await conv.decide("approve", gate="plan")


async def test_a_text_card_takes_other_text_and_you_decide_and_skip_are_taps(
    api: ApiClient,
) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    await conv.say("An online shop for handmade candles")
    await conv.tap(you_decide=True)
    await conv.tap(skipped=True)
    assert conv.snap is not None and conv.snap.pending_card is not None


async def test_an_upload_and_a_ui_action(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    reply = await conv.upload("notes.md", "We sell handmade candles online and want an app.")
    assert any(isinstance(e, AssistantMessage | CardEvent) for e in reply)
    finished = await conv.ui("finish_with_defaults")
    assert conv.state == "BRIEF_REVIEW" and any(isinstance(e, GateEvent) for e in finished)


async def test_the_answer_loop_stops_at_its_limit(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    await conv.say("An online shop for handmade candles")
    with pytest.raises(ConversationError, match="did not finish within 1 questions"):
        await conv.answer_with_recommended(limit=1)


async def test_both_gates_to_spec_ready_and_waiting_for_the_plan(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    await conv.say("An online shop for handmade candles")
    await conv.answer_with_recommended()
    await conv.decide("approve", gate="brief")
    assert conv.state == "PLANNING"
    snap = await conv.wait_planning(30)
    assert snap.state == "PLAN_REVIEW"
    assert any(isinstance(e, ProgressEvent) for e in conv.log)
    plan_gate = conv.last_gate
    assert plan_gate is not None and plan_gate.gate == "plan"
    await conv.decide("approve", gate="plan")
    assert conv.state == "HANDED_OFF"
    ready = [e for e in conv.log if isinstance(e, SpecReady)]
    assert len(ready) == 1 and ready[0].hash == plan_gate.hash


async def test_waiting_gives_up_after_its_time_and_says_how_to_come_back(
    api: ApiClient, state: AppState
) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    await conv.say("An online shop for handmade candles")
    await conv.answer_with_recommended()
    await state.stop_worker()  # nothing will run the job
    await conv.decide("approve", gate="brief")
    with pytest.raises(PlanTimeout, match=f"--session {conv.session_id}"):
        await conv.wait_planning(0.4)
    assert conv.state == "PLANNING"


async def test_a_session_is_resumed_where_it_stands_without_replaying_the_past(
    api: ApiClient,
) -> None:
    first = Conversation(api)
    await first.start("guided")
    await first.say("An online shop for handmade candles")
    again = Conversation(api, on_event=lambda e: pytest.fail(f"replayed {e.seq}"))
    snap = await again.resume(first.session_id)
    assert snap.pending_card is not None and again.contiguous == snap.last_seq
    assert await again.drain() == []  # nothing newer
    other = await first.say("more about candles")  # events appear for the other reader too
    assert other


async def test_watching_shows_what_arrives_and_refreshes_the_state(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    assert await conv.watch_for(1.0) == []
    assert conv.snap is not None


async def test_an_enterprise_plan_needs_every_required_person(api: ApiClient) -> None:
    conv = Conversation(api)
    await conv.start("enterprise", "enterprise-it-services-firm")
    await conv.say("A company app for our field staff: checklists and approvals")
    await conv.answer_with_recommended()
    await conv.decide("approve", gate="brief")  # Gate 1 needs the requester only
    await conv.wait_planning(30)
    gate = conv.last_gate
    assert gate is not None and gate.gate == "plan" and len(gate.required_roles) > 1
    others = [role for role in gate.required_roles if role != "requester"]
    await conv.decide("approve")  # "Send for approval": the owner submits, the named people decide
    assert conv.state == "PLAN_REVIEW"  # not handed off: nobody has approved yet
    assert any(isinstance(e, Notice) and e.code == "submitted_for_approval" for e in conv.log)
    for role in others:
        await conv.decide("approve", role=role, identity=Identity("tenant-a", f"u-{role}", (role,)))
    assert conv.state == "HANDED_OFF"
    assert len([e for e in conv.log if isinstance(e, SpecReady)]) == 1
