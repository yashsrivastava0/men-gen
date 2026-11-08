"""BUG-6: a builder question must always be able to resume the run.

The real Discover engine answers a typed reply or a tap on a builder question by *clearing* the
card, even when the answer is the value the slot already had (nothing changed) and even when the
reply was only chatter (it treats the card as answered once its slot has a value). The flow used to
resume only when a slot changed, so those replies stranded the session in DISCOVERING with no card,
no run and no gate. These tests script the engine's behaviour and run over both stores.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from mde_contracts.calls import Intent
from mde_contracts.cards import Card, CardInstance, CardOption, CardProgress, SkipSpec
from mde_contracts.engines import (
    BuildRequest,
    BuildResult,
    DiscoveryRequest,
    DiscoveryResult,
    RunContext,
)
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    InboundEvent,
    Notice,
    OptionSelected,
    OutboundEvent,
    UserMessage,
)
from mde_flow import bookkeeping, messages
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


def question(kit: FlowKit, *, skippable: bool = True) -> CardInstance:
    card = Card(
        id="Q-build-default",
        slot="scope.features",
        type="choice3",
        text="We could not fit everything into a working plan. How would you like to start?",
        options=[
            CardOption(
                id="A",
                label="Start with the main features",
                recommended=True,
                sets={"scope.features": ["browse", "search"]},
            ),
            CardOption(
                id="B", label="Start with the essentials", sets={"scope.features": ["browse"]}
            ),
            CardOption(id="C", label="I will choose myself", sets={}),
        ],
        skip=SkipSpec(enabled=skippable, assumes="A"),
        kind="resolution",
    )
    return CardInstance(
        instance_id=kit.ids("crd"), card=card, progress=CardProgress(asked=1, cap=10)
    )


def ask_once(kit: FlowKit, *, skippable: bool = True) -> CardInstance:
    """The builder's first run ends with this question; every later run builds normally."""
    card = question(kit, skippable=skippable)
    real = kit.builder.build
    asked: list[bool] = []

    async def build(req: BuildRequest, ctx: RunContext) -> BuildResult:
        if not asked:
            asked.append(True)
            return BuildResult(status="needs_answer", question=card, question_slot="scope.features")
        return await real(req, ctx)

    kit.builder.build = build  # type: ignore[method-assign]
    return card


def real_engine_like(
    kit: FlowKit, *, intent: Intent, changed: list[str] | None = None
) -> Callable[[DiscoveryRequest, InboundEvent], DiscoveryResult | None]:
    """Model the real engine for a reply to a builder question: it clears the pending card
    (the slot already has a value) and reports what it read the reply as."""

    def override(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        if req.state.pending_card is None:
            return None
        state = req.state.model_copy(update={"pending_card": None})
        events: list[OutboundEvent] = [
            AssistantMessage(
                event_id=kit.ids("evt"), session_id=req.session_id, text="Got it.", canned=True
            )
        ]
        return DiscoveryResult(
            state=state, events=events, intent=intent, changed_slots=list(changed or [])
        )

    return override


async def at_the_question(kit: FlowKit) -> tuple[Driver, CardInstance]:
    card = ask_once(kit)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build" and snap.pending_card
    return d, card


async def stored(kit: FlowKit, d: Driver):  # type: ignore[no-untyped-def]
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id)
    assert rec is not None
    return rec


def notice_codes(events: list) -> list[str]:  # type: ignore[type-arg]
    return [e.code for e in events if isinstance(e, Notice)]


async def test_an_answer_equal_to_the_current_value_still_resumes_the_run() -> None:
    kit = make_flow_kit()
    d, card = await at_the_question(kit)
    kit.discovery.override = real_engine_like(kit, intent="answer")  # nothing changed, card cleared
    out = await d.send(
        OptionSelected(event_id=d.eid(), card_instance_id=card.instance_id, option_ids=["A"])
    )
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "build"
    assert messages.RESUMING_PLAN in [e.text for e in out if isinstance(e, AssistantMessage)]
    # the question's own slot is what the run was resumed with, though no slot changed
    job = (await kit.peek.jobs(d.session_id))[-1]
    assert job.payload.answer_slot_ids == ["scope.features"]
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert "scope.features" in kit.builder.calls[-1].answer_slots


async def test_chatter_keeps_the_question_pending_with_a_reminder() -> None:
    kit = make_flow_kit()
    d, card = await at_the_question(kit)
    kit.discovery.override = real_engine_like(kit, intent="other")  # engine drops the card
    out = await d.say("hmm")
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build"
    assert snap.pending_card is not None and snap.pending_card.instance_id == card.instance_id
    assert notice_codes(out) == [messages.N_ANSWER_NEEDED]
    shown = [e for e in out if isinstance(e, CardEvent)]
    assert [e.card.instance_id for e in shown] == [card.instance_id]  # the question is shown again
    assert not [j for j in await kit.peek.jobs(d.session_id) if j.state in ("queued", "running")]


async def test_the_default_is_offered_after_three_unanswered_replies_and_resumes_the_run() -> None:
    kit = make_flow_kit()
    d, _ = await at_the_question(kit)
    chatter = real_engine_like(kit, intent="chitchat")
    kit.discovery.override = chatter
    for n in range(2):
        out = await d.say(f"lol {n}")
        assert notice_codes(out) == [messages.N_ANSWER_NEEDED]
    out = await d.say("lol 3")
    offer = [e for e in out if isinstance(e, AssistantMessage)]
    assert [m.text for m in offer if m.chips] == [messages.ANSWER_OFFER_DEFAULT]
    assert offer[-1].chips == [messages.CHIP_USE_DEFAULT]
    assert (await d.snapshot()).state == "DISCOVERING"
    # saying "Use the default" is a tap on "you decide" for the question: the engine takes the
    # recommended option and the run resumes
    kit.discovery.override = None
    await d.say("use the default!")
    assert kit.discovery.calls[-1] == ("handle", "option_selected")
    snap = await d.snapshot()
    assert snap.state == "PLANNING" and snap.plan_phase == "build"
    rec = await stored(kit, d)
    assert bookkeeping.ANSWER_TRIES not in rec.discovery.extra  # the counters are gone
    assert bookkeeping.ANSWER_OFFER not in rec.discovery.extra
    await d.run_job()
    assert (await d.snapshot()).state == "PLAN_REVIEW"


async def test_use_the_default_is_ordinary_text_until_it_has_been_offered() -> None:
    kit = make_flow_kit()
    d, _ = await at_the_question(kit)
    kit.discovery.override = real_engine_like(kit, intent="other")
    out = await d.say("use the default")
    assert kit.discovery.calls[-1] == ("handle", "user_message:use the default")
    assert notice_codes(out) == [messages.N_ANSWER_NEEDED]
    assert (await d.snapshot()).state == "DISCOVERING"


async def test_a_card_that_cannot_be_skipped_is_never_defaulted() -> None:
    kit = make_flow_kit()
    card = ask_once(kit, skippable=False)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert (await d.snapshot()).pending_card is not None
    kit.discovery.override = real_engine_like(kit, intent="other")
    for _ in range(4):
        out = await d.say("hmm")
        assert notice_codes(out) == [messages.N_ANSWER_NEEDED]  # a reminder, never an offer
        assert not [e for e in out if isinstance(e, AssistantMessage) and e.chips]
    rec = await stored(kit, d)
    assert rec.discovery.pending_card is not None
    assert rec.discovery.pending_card.instance_id == card.instance_id
    assert bookkeeping.ANSWER_OFFER not in rec.discovery.extra


async def test_a_follow_up_question_comes_before_the_run_resumes() -> None:
    kit = make_flow_kit()
    d, card = await at_the_question(kit)
    follow_up = question(kit)
    follow_up = follow_up.model_copy(
        update={"card": follow_up.card.model_copy(update={"id": "Q-follow-up"})}
    )

    def override(req: DiscoveryRequest, event: InboundEvent) -> DiscoveryResult | None:
        state = req.state.model_copy(update={"pending_card": follow_up})
        return DiscoveryResult(state=state, intent="answer", changed_slots=["scope.features"])

    kit.discovery.override = override
    out = await d.send(
        OptionSelected(event_id=d.eid(), card_instance_id=card.instance_id, option_ids=["C"])
    )
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.pending_card is not None
    assert snap.pending_card.card.id == "Q-follow-up"
    assert notice_codes(out) == []  # not a non-answer: the engine simply has one more question


async def test_a_session_stranded_by_the_old_behaviour_resumes_on_the_next_message() -> None:
    kit = make_flow_kit()
    d, _ = await at_the_question(kit)
    async with kit.store.begin(d.who.tenant_id) as uow:  # what BUG-6 left behind
        rec = await uow.sessions.get(d.session_id, for_update=True)
        assert rec is not None
        stranded = rec.discovery.model_copy(update={"pending_card": None})
        await uow.sessions.save(
            rec.model_copy(update={"discovery": stranded}), expected_version=rec.version
        )
    snap = await d.snapshot()
    assert snap.state == "DISCOVERING" and snap.plan_phase == "build" and snap.pending_card is None
    await d.say("set design.direction=bold-dark")
    assert (await d.snapshot()).state == "PLANNING"
    await d.run_job()
    assert kit.builder.calls[-1].slots["design.direction"].value == "bold-dark"


async def test_an_edit_of_another_slot_is_not_a_wasted_reply() -> None:
    kit = make_flow_kit()
    d, card = await at_the_question(kit)
    for _ in range(4):  # edits never count towards the offer: they are progress
        out = await d.say("set design.direction=bold-dark")
        assert notice_codes(out) == [messages.N_ANSWER_NEEDED]
    rec = await stored(kit, d)
    assert rec.discovery.pending_card is not None
    assert rec.discovery.pending_card.instance_id == card.instance_id
    assert bookkeeping.ANSWER_OFFER not in rec.discovery.extra
    assert not [m for m in d.of_type(AssistantMessage) if m.chips and "default" in m.text]


async def test_typed_text_that_was_read_as_an_answer_resumes_even_with_nothing_changed() -> None:
    kit = make_flow_kit()
    d, _ = await at_the_question(kit)
    kit.discovery.override = real_engine_like(kit, intent="volunteered")
    await d.send(UserMessage(event_id=d.eid(), text="the main features please"))
    assert (await d.snapshot()).state == "PLANNING"
