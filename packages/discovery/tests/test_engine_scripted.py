"""Engine behaviour driven by exact (scripted) model outputs: odd, hostile or rare results."""

from __future__ import annotations

from mde_contracts.calls import C1Output, C14Output, ChangeOp, Reference, SlotUpdate
from mde_contracts.events import AssistantMessage, CardEvent, Notice
from mde_discovery.testing import Conversation, ScriptedGateway


def c1(intent: str = "other", confidence: float = 0.9, **kwargs: object) -> C1Output:
    return C1Output.model_validate(
        {"intent": intent, "confidence": confidence, "language": "en", **kwargs}
    )


async def started(gateway: ScriptedGateway, policy: str = "guided-default") -> Conversation:
    conv = Conversation(gateway=gateway, policy=policy)
    await conv.start()
    await conv.say("An online shop")
    return conv


def say_texts(conv: Conversation) -> list[str]:
    return [e.text for e in conv.last.events if isinstance(e, AssistantMessage)]


async def test_control_intents_returned_by_c1_set_the_same_flags_as_the_fast_paths() -> None:
    for intent, flag in (
        ("stop", "stop_requested"),
        ("pause", "pause_requested"),
        ("start_over", "start_over_requested"),
    ):
        conv = await started(ScriptedGateway({"C1.understand": [c1(intent)]}))
        await conv.say("a long way of saying it that the router does not know")
        assert getattr(conv.last, flag) and conv.last.intent == intent


async def test_a_greeting_returned_by_c1_shows_the_greeting_and_the_pending_card() -> None:
    conv = await started(ScriptedGateway({"C1.understand": [c1("greeting")]}))
    await conv.say("good day to you my dear assistant")
    assert any(isinstance(e, AssistantMessage) and len(e.chips) == 6 for e in conv.last.events)
    assert any(isinstance(e, CardEvent) for e in conv.last.events)


async def test_unsafe_intent_without_flags_is_still_declined() -> None:
    conv = await started(ScriptedGateway({"C1.understand": [c1("unsafe")]}))
    await conv.say("something the model did not like")
    assert conv.last.intent == "unsafe" and "cannot help build that" in say_texts(conv)[0]
    assert conv.pending is not None  # back to the question afterwards


async def test_negations_from_the_model_clear_or_trim_slots() -> None:
    gateway = ScriptedGateway(
        {
            "C1.understand": [
                c1(
                    "volunteered",
                    slot_updates=[
                        SlotUpdate(slot_id="scope.features", value_list=["search", "reviews"]),
                    ],
                ),
                c1(
                    "volunteered",
                    slot_updates=[
                        SlotUpdate(slot_id="scope.features", value_list=["reviews"], negate=True),
                    ],
                ),
                c1(
                    "volunteered",
                    slot_updates=[SlotUpdate(slot_id="scope.features", negate=True)],
                ),
            ]
        }
    )
    conv = await started(gateway)
    await conv.say("and also search and reviews, one")
    assert conv.value("scope.features") == ["search", "reviews"]
    await conv.say("no reviews, two")
    assert conv.value("scope.features") == ["search"]
    await conv.say("nothing at all, three")
    assert conv.slot("scope.features") is None


async def test_references_from_the_model_are_noted_once() -> None:
    gateway = ScriptedGateway(
        {
            "C1.understand": [
                c1("volunteered", references=[Reference(kind="like_app", text="Zomato")]),
                c1("volunteered", references=[Reference(kind="like_app", text="Zomato")]),
                c1("volunteered", references=[Reference(kind="region", text="Pune")]),
            ]
        }
    )
    conv = await started(gateway)
    for text in ("like zomato please", "again like zomato", "in pune"):
        await conv.say(text)
    assert conv.state.references == ["Zomato"]


async def test_a_value_that_fits_nothing_is_dropped_and_the_user_is_asked_again() -> None:
    gateway = ScriptedGateway(
        {
            "C1.understand": [
                c1(
                    "volunteered",
                    slot_updates=[SlotUpdate(slot_id="auth.method", value_text="nope")],
                )
            ]
        }
    )
    conv = await started(gateway)
    await conv.say("some words that mean nothing in particular")
    assert conv.slot("auth.method") is None
    assert "I did not catch that" in say_texts(conv)[0]
    assert conv.pending is not None and conv.pending.card.id == "Q-type-01"


async def test_a_c1_approval_at_a_gate_with_slot_values_is_a_change_not_an_approval() -> None:
    conv = await started(ScriptedGateway())
    await conv.drive_recommended()
    conv.session_state, conv.gate_open = "BRIEF_REVIEW", "brief"
    conv.gateway = ScriptedGateway(
        {
            "C1.understand": [
                c1(
                    "approve",
                    0.9,
                    slot_updates=[
                        SlotUpdate(slot_id="design.direction", value_text="warm-friendly")
                    ],
                )
            ]
        }
    )
    conv.engine.understander.llm._gateway = conv.gateway  # pyright: ignore[reportPrivateUsage]
    await conv.say("yes, but warm")
    assert conv.last.intent == "change" and not conv.last.approval_confident
    assert conv.value("design.direction") == "warm-friendly"
    assert conv.last.change_ops[0].slot_id == "design.direction"


async def test_a_volunteered_update_at_a_gate_is_applied_as_a_change() -> None:
    conv = await started(ScriptedGateway())
    await conv.drive_recommended()
    conv.session_state, conv.gate_open = "BRIEF_REVIEW", "brief"
    conv.swap_gateway(
        ScriptedGateway(
            {
                "C1.understand": [
                    c1(
                        "volunteered",
                        slot_updates=[
                            SlotUpdate(slot_id="behavior.analytics", value_bool=True),
                            SlotUpdate(slot_id="scope.features", value_list=["reviews", "loyalty"]),
                            SlotUpdate(slot_id="brand.assets", negate=True),
                        ],
                    )
                ]
            }
        )
    )
    await conv.say("track usage and add loyalty and reviews")
    assert conv.last.intent == "change"
    kinds = {(o.slot_id, o.kind) for o in conv.last.change_ops}
    assert ("behavior.analytics", "set_slot") in kinds and ("scope.features", "set_slot") in kinds
    assert conv.value("behavior.analytics") is True
    assert "loyalty" in conv.items("scope.features")


async def test_c14_can_say_the_text_was_an_approval() -> None:
    gateway = ScriptedGateway(
        {
            "C1.understand": [c1("change", 0.8)],
            "C14.interpret_change": [C14Output(understood=True, is_approval=True, confidence=0.9)],
        }
    )
    conv = await started(gateway)
    await conv.drive_recommended()
    conv.session_state, conv.gate_open = "BRIEF_REVIEW", "brief"
    await conv.say("make my day and ship it already")
    assert conv.last.intent == "approve" and conv.last.approval_confident


async def test_c14_ops_that_touch_no_slot_are_reported_to_the_flow_unchanged() -> None:
    ops = [ChangeOp(kind="patch_screen", target_id="SCR-002", instruction="move the price up")]
    gateway = ScriptedGateway(
        {
            "C1.understand": [c1("change", 0.8)],
            "C14.interpret_change": [C14Output(understood=True, ops=ops, confidence=0.8)],
        }
    )
    conv = await started(gateway)
    await conv.say("move the price up on the product screen")
    assert conv.last.change_ops == ops and conv.last.changed_slots == []
    assert (
        "screen" in conv.state.extra["screen_notes"] or "price" in conv.state.extra["screen_notes"]
    )


async def test_c14_not_understood_asks_its_clarifying_question() -> None:
    gateway = ScriptedGateway(
        {
            "C1.understand": [c1("change", 0.8)],
            "C14.interpret_change": [
                C14Output(understood=False, clarify_question="Which screen do you mean?")
            ],
        }
    )
    conv = await started(gateway)
    await conv.say("change it")
    assert say_texts(conv) == ["Which screen do you mean?"]
    assert conv.pending is not None


async def test_a_flagged_domain_left_unanswered_is_assumed_careful_when_finishing_in_guided() -> (
    None
):
    conv = Conversation()
    await conv.start()
    await conv.say("I want a clinic app where patients book appointments")
    assert conv.pending is not None and conv.state.sensitive_flags == ["health"]
    await conv.ui("finish_with_defaults")
    assert conv.last.brief_complete
    flags = conv.slot("compliance.flags")
    assert flags is not None and flags.state == "assumed" and flags.value == ["health_data_privacy"]
    assert conv.state.compliance_confirmed == ["health"]
    assert conv.value("behavior.analytics") is False  # the conservative default came with it


async def test_typing_keep_going_answers_the_finish_offer_card() -> None:
    conv = await started(ScriptedGateway())
    for _ in range(3):
        await conv.tap(you_decide=True)
    assert conv.pending is not None and conv.pending.card.id == "Q-nudge-02"
    await conv.say("keep going")
    assert conv.state.skips_in_row == 0 and conv.pending is not None
    assert conv.pending.card.id != "Q-nudge-02"


async def test_typing_finish_with_defaults_while_the_offer_is_pending_finishes() -> None:
    conv = await started(ScriptedGateway())
    for _ in range(3):
        await conv.tap(you_decide=True)
    await conv.say("finish with smart defaults")
    assert conv.last.brief_complete and conv.pending is None


async def test_you_decide_on_the_finish_offer_card_finishes() -> None:
    conv = await started(ScriptedGateway())
    for _ in range(3):
        await conv.tap(you_decide=True)
    await conv.say("you decide")
    assert conv.last.brief_complete


async def test_an_idea_card_cannot_be_skipped() -> None:
    conv = Conversation()
    await conv.start()
    await conv.ui("finish_with_defaults")
    await conv.tap(you_decide=True)
    assert conv.pending is not None and conv.pending.card.id == "Q-idea-01"
    assert "did not catch" in say_texts(conv)[0]
    await conv.tap(skipped=True)
    assert conv.pending is not None and conv.pending.card.id == "Q-idea-01"


async def test_a_second_upload_after_the_idea_extends_the_slots() -> None:
    conv = await started(ScriptedGateway())
    long_text = "We want search and a dark look. " + "More about the shop. " * 40
    await conv.upload("more.md", long_text)
    assert any(isinstance(e, AssistantMessage) and "more.md" in e.text for e in conv.last.events)
    assert conv.gateway.call_ids()[-2:] == ["C1.understand", "C2.interpret_brief"]
    assert conv.state.uploads[-1].filename == "more.md"


async def test_progress_counts_only_real_questions() -> None:
    conv = await started(ScriptedGateway())
    first = conv.cards()[-1]
    assert first.progress is not None and (first.progress.asked, first.progress.cap) == (1, 10)
    await conv.say("pata nahi")  # help: not a counted card
    assert conv.state.card_count == 1
    notice = [e for e in conv.events if isinstance(e, Notice)]
    assert notice == []
