"""The DiscoveryEngine protocol: start, handle, reopen_slot, apply_changes, compile_brief."""

from __future__ import annotations

from mde_content.catalogs import load_catalogs
from mde_contracts.brief import Brief
from mde_contracts.calls import ChangeOp
from mde_contracts.engines import DiscoveryEngine
from mde_contracts.events import AssistantMessage, CardEvent, GateDecision, Notice
from mde_contracts.slots import SlotRecord
from mde_discovery.engine import DiscoverEngine, build_engine
from mde_discovery.testing import (
    Conversation,
    DirectMockGateway,
    make_request,
    make_run_context,
)


async def finished(policy: str = "guided-default", lang: str | None = None) -> Conversation:
    conv = Conversation(policy=policy, language_hint=lang)
    await conv.start()
    chip = "An online shop" if lang is None else conv.engine.banks.chips("commerce", lang)[0].label
    await conv.say(chip)
    await conv.drive_recommended()
    assert conv.last.brief_complete
    return conv


def test_the_engine_implements_the_protocol() -> None:
    engine: DiscoveryEngine = DiscoverEngine(DirectMockGateway())
    for name in ("start", "handle", "reopen_slot", "apply_changes", "compile_brief"):
        assert callable(getattr(engine, name))
    assert isinstance(build_engine(DirectMockGateway()), DiscoverEngine)


async def test_start_is_the_canned_greeting_and_changes_only_the_greeted_flag() -> None:
    engine = DiscoverEngine(DirectMockGateway())
    req = make_request()
    result = await engine.start(req, make_run_context())
    assert result.state.greeted and result.llm_calls == 0
    assert result.state.slots == {} and result.state.card_count == 0
    assert result.state.recent_turns[-1].role == "assistant"
    [event] = result.events
    assert isinstance(event, AssistantMessage) and event.canned and len(event.chips) == 6
    assert event.session_id == req.session_id and event.event_id.startswith("evt_")


async def test_the_engine_never_mutates_the_state_it_was_given() -> None:
    conv = await finished()
    req = conv.request()
    snapshot = req.state.model_dump_json()
    await conv.engine.handle(req, _message("make it dark"), make_run_context())
    assert req.state.model_dump_json() == snapshot


async def test_replies_are_linked_to_the_inbound_event() -> None:
    conv = Conversation()
    await conv.start()
    event = _message("An online shop")
    result = await conv.engine.handle(conv.request(), event, make_run_context())
    assert result.events and all(e.in_reply_to == event.event_id for e in result.events)


async def test_a_gate_decision_is_not_discovers_business() -> None:
    conv = await finished()
    event = GateDecision(
        event_id="evt_g", gate="brief", decision="approve", version=1, hash="a" * 64
    )
    result = await conv.engine.handle(conv.request(), event, make_run_context())
    assert result.events == [] and result.state.slots == conv.state.slots
    assert result.intent == "other"


async def test_the_turn_counter_counts_events_that_cost_a_turn() -> None:
    conv = Conversation()
    await conv.start()
    assert conv.state.turn_count == 0
    await conv.say("An online shop")
    await conv.tap_recommended()
    assert conv.state.turn_count == 2
    await conv.ui("resume")  # a button that is not an answer does not use a turn
    assert conv.state.turn_count == 2


# ----------------------------------------------------------------------------- reopen_slot


async def test_reopen_slot_shows_the_card_of_one_slot_only() -> None:
    conv = await finished()
    count = conv.state.card_count
    asked = list(conv.state.cards_asked)
    result = await conv.engine.reopen_slot(conv.request(), "design.direction", make_run_context())
    cards = [e for e in result.events if isinstance(e, CardEvent)]
    assert [c.card.card.id for c in cards] == ["Q-look-01"]
    assert result.state.pending_card is not None and result.intent == "change"
    assert result.state.card_count == count and result.state.cards_asked == asked  # not counted
    assert result.llm_calls == 0 and result.state.extra["reopened"] == "design.direction"
    assert not result.brief_complete  # a card is pending
    conv.state = result.state
    await conv.tap("B")
    assert (
        conv.value("design.direction") == "bold-dark"
        and conv.state_of("design.direction") == "confirmed"
    )  # type: ignore[union-attr]
    assert conv.last.brief_complete and conv.pending is None
    assert "reopened" not in conv.state.extra


async def test_reopen_slot_finds_cards_for_other_slots_and_domains() -> None:
    conv = await finished()
    for slot, card_id in (
        ("auth.method", "Q-auth-01"),
        ("scope.features", "Q-feat-01"),
        ("idea.summary", "Q-idea-01"),
    ):
        result = await conv.engine.reopen_slot(conv.request(), slot, make_run_context())
        assert (
            result.state.pending_card is not None and result.state.pending_card.card.id == card_id
        )
    flagged = Conversation()
    await flagged.start()
    await flagged.say("I want a clinic app where patients book appointments")
    result = await flagged.engine.reopen_slot(
        flagged.request(), "compliance.flags", make_run_context()
    )
    assert (
        result.state.pending_card is not None and result.state.pending_card.card.id == "Q-comp-01"
    )


async def test_reopen_slot_without_a_bank_card_asks_c5_for_one() -> None:
    conv = await finished()
    result = await conv.engine.reopen_slot(conv.request(), "brand.assets", make_run_context())
    assert result.llm_calls == 1 and result.state.pending_card is not None
    assert (
        result.state.pending_card.generated
        and result.state.pending_card.card.slot == "brand.assets"
    )


async def test_a_reopened_multi_card_is_pre_ticked_from_the_current_value() -> None:
    conv = await finished()
    current = conv.items("scope.features")
    result = await conv.engine.reopen_slot(conv.request(), "scope.features", make_run_context())
    assert result.state.pending_card is not None
    ticked = [o.id for o in result.state.pending_card.card.options if o.recommended]
    assert set(ticked) == set(current)


# ----------------------------------------------------------------------------- apply_changes


async def test_apply_changes_toggles_a_feature_and_keeps_the_brief_complete() -> None:
    conv = await finished()
    op = ChangeOp(kind="toggle_feature", target_id="reviews", value_bool=True)
    result = await conv.engine.apply_changes(conv.request(), [op], "tap", make_run_context())
    assert "reviews" in items(result.state.slots["scope.features"])
    assert result.changed_slots == ["scope.features"] and result.change_ops == [op]
    assert result.brief_complete and result.state.pending_card is None
    off = ChangeOp(kind="toggle_feature", target_id="reviews", value_bool=False)
    conv.state = result.state
    again = await conv.engine.apply_changes(conv.request(), [off], "tap", make_run_context())
    assert "reviews" not in items(again.state.slots["scope.features"])


async def test_switching_off_an_assumption_asks_the_question_instead() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.ui("finish_with_defaults")
    assert conv.state_of("auth.method") == "assumed"
    op = ChangeOp.model_validate(
        {
            "kind": "other",
            "slot_id": "auth.method",
            "value_bool": False,
            "instruction": "toggle_assumption",
        }
    )
    result = await conv.engine.apply_changes(conv.request(), [op], "tap", make_run_context())
    assert "auth.method" not in {k for k, v in result.state.slots.items() if v.state != "empty"}
    assert (
        result.state.pending_card is not None
        and result.state.pending_card.card.slot == "auth.method"
    )
    assert not result.brief_complete


async def test_apply_changes_sets_and_clears_slots_and_ignores_what_does_not_fit() -> None:
    conv = await finished()
    ops = [
        ChangeOp(kind="set_slot", slot_id="design.direction", value_text="warm-friendly"),
        ChangeOp(kind="set_slot", slot_id="auth.method", value_text="telepathy"),  # not allowed
        ChangeOp(kind="toggle_feature", target_id="no_such_feature", value_bool=True),
        ChangeOp(kind="clear_slot", slot_id="design.accent"),
        ChangeOp(kind="add_screen", instruction="a store locator"),  # a plan operation: not ours
    ]
    result = await conv.engine.apply_changes(conv.request(), ops, "text", make_run_context())
    assert result.state.slots["design.direction"].value == "warm-friendly"
    assert result.state.slots["auth.method"].value == "email_phone_code"
    assert result.state.slots["design.direction"].state == "confirmed"
    assert result.state.slots["design.direction"].source == "text"
    assert result.change_ops == ops and result.intent == "change"


async def test_a_change_of_type_through_apply_changes_drops_what_belongs_to_the_old_type() -> None:
    conv = await finished()
    op = ChangeOp(kind="set_slot", slot_id="idea.archetype", value_text="booking_services")
    result = await conv.engine.apply_changes(conv.request(), [op], "tap", make_run_context())
    features = result.state.slots.get("scope.features")
    assert result.state.slots["idea.archetype"].value == "booking_services"
    pack = {
        "service_catalog",
        "provider_profiles",
        "slot_booking",
        "payments",
        "reminders",
        "reviews",
        "reschedule_cancel",
        "push_notifications",
        "memberships",
        "support",
    }
    assert features is None or set(items(features)) <= pack
    apps = result.state.slots.get("scope.apps")
    assert apps is None or set(items(apps)) <= {
        "main",
        "customer",
        "business",
        "provider",
        "admin",
    }


async def test_the_ui_toggle_buttons_work_through_handle_too() -> None:
    conv = await finished()
    await conv.ui("toggle_feature", "reviews", True)
    assert "reviews" in conv.items("scope.features")
    assert conv.last.change_ops and conv.last.intent == "change"
    await conv.ui("toggle_assumption", "auth.method", False)
    assert conv.slot("auth.method") is None
    assert conv.pending is not None and conv.pending.card.slot == "auth.method"
    await conv.ui("edit_row", "design.direction")
    assert conv.pending is not None and conv.pending.card.slot == "design.direction"
    await conv.ui("edit_row", "not.a.slot")  # ignored
    await conv.ui("toggle_assumption")  # no target: ignored
    await conv.ui("undo")


# ----------------------------------------------------------------------------- compile_brief


async def test_the_brief_has_six_rows_in_order_each_editing_its_first_slot() -> None:
    conv = await finished()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert isinstance(brief, Brief) and Brief.model_validate(brief.model_dump()) == brief
    assert [r.key for r in brief.rows] == [
        "building",
        "users",
        "features",
        "look",
        "login",
        "name_logo",
    ]
    assert [r.title for r in brief.rows] == [
        "What you are building",
        "Who uses it",
        "Main features",
        "Look",
        "Login",
        "Name and logo",
    ]
    assert [r.slot_ids[0] for r in brief.rows] == [
        "idea.summary",
        "scope.apps",
        "scope.features",
        "design.direction",
        "auth.method",
        "brand.name",
    ]
    assert brief.version == 0 and brief.hash == "" and brief.status == "draft"
    assert brief.mode == "guided" and brief.language == "en"
    assert brief.with_hash().hash == brief.compute_hash()


async def test_the_features_row_is_a_checklist_of_every_pack_feature() -> None:
    conv = await finished()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    row = brief.row("features")
    assert row is not None and len(row.features) == 10
    on = {f.id for f in row.features if f.on}
    assert on == set(conv.items("scope.features"))
    assert {"reviews", "loyalty"} <= {f.id for f in row.features if not f.on}
    assert all(f.label for f in row.features)
    assert f"{len(on)} features" in row.text


async def test_the_look_row_has_the_swatch_of_the_chosen_theme() -> None:
    conv = await finished()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    look = brief.row("look")
    assert look is not None and look.text == "Clean and bright"
    assert len(look.swatch) == 4 and all(c.startswith("#") and len(c) == 7 for c in look.swatch)
    await conv.ui("edit_row", "design.direction")
    await conv.tap("B")
    after = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert after.row("look").swatch != look.swatch  # type: ignore[union-attr]
    assert after.row("look").text == "Bold and dark"  # type: ignore[union-attr]


async def test_the_other_rows_say_what_the_user_chose_in_plain_words() -> None:
    conv = await finished()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert brief.row("login").text == "Email or phone code"  # type: ignore[union-attr]
    assert brief.row("name_logo").text == "Name: Blue Basket. Logo: none yet, we use a placeholder."  # type: ignore[union-attr]
    assert brief.row("users").text == "The shopper app"  # type: ignore[union-attr]
    assert brief.app_summary and brief.row("building").text == brief.app_summary  # type: ignore[union-attr]
    assert conv.gateway.count("C11.write_summary") == 1


async def test_assumptions_list_every_assumed_value_with_a_one_line_reason() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()
    await conv.tap(you_decide=True)  # apps
    await conv.tap(you_decide=True)  # features
    await conv.tap(you_decide=True)  # login
    await conv.ui("finish_with_defaults")
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    slots = {a.slot_id: a for a in brief.assumptions}
    assert {"scope.apps", "scope.features", "auth.method", "design.direction", "brand.name"} <= set(
        slots
    )
    assert all(a.reason and a.label and a.value_text and a.included for a in brief.assumptions)
    assert slots["auth.method"].value_text == "Email or phone code"
    assert slots["auth.method"].label == "Sign-in"
    assert "idea.archetype" not in slots  # tapped, not assumed
    assert "behavior.analytics" not in slots  # "may" slots are defaulted quietly


async def test_enterprise_briefs_carry_the_mode_and_the_extra_assumptions() -> None:
    conv = Conversation(policy="enterprise-it-services-firm")
    await conv.start()
    await conv.say("An online shop")
    await conv.ui("finish_with_defaults")
    for _ in range(8):
        if conv.pending is None:
            break
        await conv.tap_recommended()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert brief.mode == "enterprise"
    assert {"data.source", "behavior.offline"} <= {a.slot_id for a in brief.assumptions}


async def test_the_brief_is_written_in_the_users_language() -> None:
    for lang, title in (("hi", "आप क्या बना रहे हैं"), ("hi-Latn", "Aap kya bana rahe hain")):
        conv = await finished(lang=lang)
        brief = await conv.engine.compile_brief(conv.request(), make_run_context())
        assert brief.language == lang and brief.rows[0].title == title
        assert brief.row("look").text and brief.row("login").text  # type: ignore[union-attr]
        if lang == "hi":
            assert any(ord(ch) > 0x900 for ch in brief.row("login").text)  # type: ignore[union-attr]
        reasons = [a.reason for a in brief.assumptions]
        assert all(reasons)  # (the interview by taps assumes nothing: see test_localised_brief)
        english = {sd.default_reason for sd in load_catalogs().slots}
        assert not set(reasons) & english  # the reasons are in the user's language too


async def test_a_custom_wish_appears_in_the_features_row_text() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()
    await conv.tap_recommended()
    await conv.tap(["search"], other_text="gift wrapping")
    await conv.drive_recommended()
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert "gift wrapping" in brief.row("features").text  # type: ignore[union-attr]


def test_notices_have_stable_codes() -> None:
    assert Notice(event_id="e", session_id="s", code="stale_card", text="x").level == "info"


def items(rec: SlotRecord | None) -> list[str]:
    return [str(v) for v in rec.value] if rec and isinstance(rec.value, list) else []


def _message(text: str):  # type: ignore[no-untyped-def]
    from mde_contracts.events import UserMessage

    return UserMessage(event_id="evt_manual", text=text)
