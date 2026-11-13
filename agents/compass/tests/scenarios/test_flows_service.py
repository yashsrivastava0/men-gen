"""The targeted flows through the real Service in mock mode: ranking card, references, voice read-back,
phased apps, Locked-edit confirm and the typed back-edge. Fast, offline."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mde_compass.domain import PatchOp
from mde_compass.engine.ports import ChangeProposal
from mde_compass.testing import Conversation
from mde_core.errors import NotFound

DELIVERY = "I want a food delivery app for restaurants in Pune, in Hindi and English"


async def _until(conv: Conversation, frame: str, *, limit: int = 20) -> Any:
    for _ in range(limit):
        card = conv.last_card()
        if card is not None and card.frame == frame:
            return card
        if card is None:
            break
        await conv.tap_recommended()
    raise AssertionError(f"no {frame} card within {limit} taps; last: {conv.last_card()}")


async def _frames(conv: Conversation) -> list[str]:
    return [e.payload["card"]["frame"] for e in conv.of_type("card")]


def _notices(conv: Conversation, code: str) -> list[str]:
    return [str(e.payload["text"]) for e in conv.of_type("notice") if e.payload.get("code") == code]


# --------------------------------------------------------------------------------------- BUG-3


async def test_everything_is_a_must_in_hinglish_asks_the_ranking_after_the_features(
    service,
) -> None:
    conv = await Conversation.start(service)
    await conv.say(DELIVERY + ". Sab zaroori hai.")
    features = await _until(conv, "features")
    assert all(o.ticked for o in features.options)  # everything starts ticked
    await conv.tap_recommended()
    ranking = conv.last_card()
    assert ranking.frame == "priorities" and ranking.type == "priority"
    ids = [o.id for o in ranking.options]
    await conv.answer(ids[:2])
    brief = await conv.brief()
    by_name = {f["name"]: f for f in brief["scope"]}
    first, second = ranking.options[0].label, ranking.options[1].label
    assert by_name[first]["priority"] == "must" and by_name[second]["priority"] == "must"
    rest = [f for n, f in by_name.items() if n not in (first, second)]
    assert rest and all(f["priority"] == "later" and f["scope_status"] == "later" for f in rest)
    assert _notices(conv, "priorities_set")
    assert (await _frames(conv)).count("priorities") == 1  # asked once, never again


async def test_no_cue_no_ranking_card(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(DELIVERY)
    await conv.drive_to_review()
    assert "priorities" not in await _frames(conv)


async def test_the_ranking_card_cannot_be_skipped(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(DELIVERY + ". everything is a must")
    await _until(conv, "features")
    await conv.tap_recommended()
    assert conv.last_card().frame == "priorities"
    n_before = len(conv.of_type("card"))
    await conv.skip()
    assert len(conv.of_type("card")) == n_before + 1  # the same card again, with a notice
    assert conv.last_card().frame == "priorities"
    assert not conv.of_type("error")


async def test_a_junk_answer_to_the_ranking_changes_nothing_and_asks_again(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(DELIVERY + ". everything is a must")
    await _until(conv, "features")
    await conv.tap_recommended()
    ranking = conv.last_card()
    assert ranking.frame == "priorities"
    before = (await conv.brief())["scope"]
    await conv.answer([])  # nothing ranked
    assert conv.last_card().id == ranking.id and _notices(conv, "not_understood")
    assert (await conv.brief())["scope"] == before  # nobody was pushed to "later"
    assert not conv.of_type("error")


# --------------------------------------------------------------------------------------- BUG-6


async def _own_business_conversation(service) -> Conversation:
    conv = await Conversation.start(service)
    await conv.say("I own Swiggy and want a new delivery app for Pune restaurants")
    await _until(conv, "apps_you_like")
    return conv


async def test_a_named_business_is_not_a_reference_until_confirmed(service) -> None:
    conv = await _own_business_conversation(service)
    refs = (await conv.brief())["references"]
    assert refs and refs[0]["label"] == "Swiggy" and refs[0]["tier"] != "Locked"
    assert _notices(conv, "reference_check")
    card = conv.last_card()
    assert [o.id for o in card.options] == ["own", "like"] and "Swiggy" in card.question


async def test_typed_words_on_the_confirm_card_never_make_it_a_reference(service) -> None:
    conv = await _own_business_conversation(service)
    card = conv.last_card()
    await conv.answer(None, custom="it is a competitor of mine")
    assert conv.last_card().id == card.id and _notices(conv, "not_understood")
    assert (await conv.brief())["references"][0]["status"] != "confirmed"


async def test_words_typed_on_the_form_count_as_what_to_borrow(service) -> None:
    conv = await Conversation.start(service)
    await conv.say("I want a food delivery app in Pune, like Swiggy")
    form = await _until(conv, "apps_you_like")
    await conv.answer({}, custom="live tracking, quick re-order")
    ref = (await conv.brief())["references"][0]
    assert ref["borrow"] == ["live tracking", "quick re-order"] and ref["status"] == "confirmed"
    assert form.type == "quick_form"


async def test_answering_it_is_my_own_business_drops_the_reference(service) -> None:
    conv = await _own_business_conversation(service)
    seen = len(conv.events)
    await conv.answer("own")
    assert (await conv.brief())["references"] == []
    assert _notices(conv, "reference_own")
    later = [e.payload["card"]["frame"] for e in conv.events[seen:] if e.type == "card"]
    assert "apps_you_like" not in later  # no borrow/avoid card for a business that is theirs


async def test_answering_an_app_i_like_confirms_it_then_asks_borrow_and_avoid_once(service) -> None:
    conv = await _own_business_conversation(service)
    await conv.answer("like")
    assert _notices(conv, "reference_confirmed")
    form = await _until(conv, "apps_you_like")
    assert form.type == "quick_form" and [o.id for o in form.options] == ["borrow_0", "avoid_0"]
    await conv.answer(
        {"borrow_0": "live order tracking, one-tap re-order", "avoid_0": "too many offers"}
    )
    ref = (await conv.brief())["references"][0]
    assert ref["label"] == "Swiggy" and ref["tier"] == "Locked" and ref["status"] == "confirmed"
    assert ref["borrow"] == ["live order tracking", "one-tap re-order"] and ref["avoid"] == [
        "too many offers"
    ]
    await conv.drive_to_review()
    assert (await _frames(conv)).count(
        "apps_you_like"
    ) == 2  # the confirm and the form, nothing more


async def test_exactly_like_warns_once_about_patterns(service) -> None:
    conv = await Conversation.start(service)
    await conv.say("I want a delivery app in Pune exactly like Zomato, same logo")
    assert (
        len(_notices(conv, "patterns_only")) == 1 and "Zomato" in _notices(conv, "patterns_only")[0]
    )
    await conv.say("yes exactly like Zomato please")
    assert len(_notices(conv, "patterns_only")) == 1  # said once


async def test_volunteered_references_are_assumed_not_locked_and_form_is_asked(service) -> None:
    conv = await Conversation.start(service)
    await conv.say("I want a food delivery app in Pune, like Swiggy but also like Notion")
    refs = (await conv.brief())["references"]
    assert {r["label"] for r in refs} == {"Swiggy", "Notion"}
    assert all(r["tier"] != "Locked" for r in refs)
    form = await _until(conv, "apps_you_like")
    assert len(form.options) == 4  # borrow and avoid for each of the two


# --------------------------------------------------------------------------------------- BUG-9

DICTATION = (
    "so i want an app for my clinic in pune where patients can book appointments and doctors can see "
    "their schedule and also pay online and get reminders on whatsapp and hindi and english both"
)


async def test_a_voice_run_on_is_read_back_numbered_and_an_item_can_be_removed(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(DICTATION, mode="voice")
    text = conv.texts()[-1]
    assert text.startswith("I heard 5 things") and "\n3. Pay online" in text
    before = (await conv.brief())["integrations"]
    await conv.say("remove 3")
    removed = conv.texts()
    assert any(t.startswith("Removed") or t.startswith("I could not find") for t in removed)
    rest = next(t for t in removed if t.startswith("Still on the list"))
    assert "Pay online" not in rest and "3. Get reminders on whatsapp" in rest  # renumbered
    assert (await conv.brief())[
        "integrations"
    ] == before  # two items and none named: nothing is cleared
    assert not conv.of_type("error")


async def test_a_short_voice_message_is_not_read_back(service) -> None:
    conv = await Conversation.start(service)
    await conv.say("I want a food delivery app in Pune", mode="voice")
    assert not any(t.startswith("I heard") for t in conv.texts())


async def test_typed_text_is_never_read_back(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(DICTATION)
    assert not any(t.startswith("I heard") for t in conv.texts())


# -------------------------------------------------------------------------------------- BUG-10


async def test_six_apps_keep_two_and_park_four_as_later_scope(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(
        "For food delivery in Pune I need a customer app, a driver app, a restaurant app, an admin panel, "
        "a support dashboard and a franchise portal"
    )
    assert len(_notices(conv, "apps_phased")) == 1
    brief = await conv.brief()
    assert [a["name"] for a in brief["apps"]] == ["customer app", "driver app"]
    later = [f for f in brief["scope"] if f["source"] == "later_phase_app"]
    assert [f["name"] for f in later] == [
        "restaurant app",
        "admin panel",
        "support dashboard",
        "franchise portal",
    ]
    assert all(f["scope_status"] == "later" and f["priority"] == "later" for f in later)


async def test_four_apps_are_all_kept(service) -> None:
    conv = await Conversation.start(service)
    await conv.say(
        "I need a customer app, a driver app, a restaurant app and an admin panel for delivery in Pune"
    )
    assert not _notices(conv, "apps_phased")
    assert not [f for f in (await conv.brief())["scope"] if f["source"] == "later_phase_app"]


# -------------------------------------------------------------------------------------- BUG-12


async def _review(service) -> Conversation:
    conv = await Conversation.start(service)
    await conv.say(DELIVERY)
    await conv.drive_to_review()
    return conv


async def test_a_locked_edit_asks_first_and_the_answer_applies_it(service) -> None:
    conv = await _review(service)
    v = (await conv.brief())["version"]
    await conv.edit("idea.business_model", "a monthly plan for restaurants")
    card = conv.last_card()
    assert (await conv.brief())["version"] == v
    assert "monthly plan for restaurants" in card.question and "research" in card.why_we_ask
    assert "Your idea" in card.why_we_ask  # the section the value sits under
    await conv.answer("A")
    brief = await conv.brief()
    assert brief["version"] == v + 1
    assert brief["idea"]["business_model"]["value"] == "a monthly plan for restaurants"


async def test_a_locked_edit_can_be_cancelled(service) -> None:
    conv = await _review(service)
    before = await conv.brief()
    await conv.edit("idea.business_model", "a monthly plan for restaurants")
    await conv.answer("B")
    after = await conv.brief()
    assert after["_hash"] == before["_hash"] and after["version"] == before["version"]


async def test_an_edit_of_a_value_that_is_not_locked_applies_at_once(service) -> None:
    conv = await _review(service)
    v = (await conv.brief())["version"]
    await conv.edit("idea.summary", "A food delivery app for Pune restaurants and their regulars")
    assert (await conv.brief())["version"] == v + 1
    assert conv.of_type("brief_patch")


async def test_editing_words_you_typed_yourself_twice_is_never_asked_about(service) -> None:
    conv = await _review(service)
    v = (await conv.brief())["version"]
    await conv.edit("idea.summary", "A food delivery app for Pune restaurants")
    await conv.edit("idea.summary", "A food delivery and pre-order app for Pune restaurants")
    assert (await conv.brief())[
        "version"
    ] == v + 2  # the typed edit is confirmed but not tap-confirmed
    assert not [
        e
        for e in conv.events
        if e.type == "card" and e.payload["card"]["frame"] == "change_variants"
    ]


class _LockedChange:
    """A change interpreter that rewrites the Locked business model (what the model would propose)."""

    async def interpret(self, ctx: Any, change: Any, brief: Any) -> ChangeProposal:
        op = PatchOp(op="set", path="idea.business_model", value="a monthly plan for restaurants")
        return ChangeProposal(
            understood=True, ops=[op], touches_locked=["idea.business_model"], confidence=0.9
        )


async def test_a_chat_change_of_a_locked_value_asks_first_too(service) -> None:
    conv = await _review(service)
    service.bind_ports(replace(service.ports, change_interpreter=_LockedChange()))
    before = await conv.brief()
    await conv.say("please change the business model to a monthly plan for restaurants")
    card = conv.last_card()
    assert card is not None and "monthly plan for restaurants" in card.question
    assert (await conv.brief())["_hash"] == before["_hash"]  # nothing applied yet
    await conv.answer("A")
    assert (await conv.brief())["idea"]["business_model"][
        "value"
    ] == "a monthly plan for restaurants"


async def test_an_equal_value_is_not_asked_about(service) -> None:
    conv = await _review(service)
    current = (await conv.brief())["idea"]["business_model"]["value"]
    n_cards = len(conv.of_type("card"))
    await conv.edit("idea.business_model", current)
    assert len(conv.of_type("card")) == n_cards


# -------------------------------------------------------------------------------------- BUG-14


async def test_the_back_edge_asks_one_question_and_the_answer_applies(service) -> None:
    conv = await _review(service)
    result = await service.reopen_slot(
        conv.caller, conv.session_id, "idea.business_model", "restaurants pay a flat monthly fee"
    )
    conv.events.extend(result.events)
    cards = [e for e in result.events if e.type == "card"]
    assert len(cards) == 1
    card = conv.last_card()
    assert "flat monthly fee" in card.question
    pick = next(o for o in card.options if o.sets)
    v = (await conv.brief())["version"]
    await conv.answer(pick.id, card=card)
    assert (await conv.brief())["version"] == v + 1


async def test_the_back_edge_runs_at_most_twice_per_session(service) -> None:
    conv = await _review(service)
    outcomes = []
    for i in range(3):
        result = await service.reopen_slot(
            conv.caller, conv.session_id, "idea.business_model", f"surprise {i}"
        )
        outcomes.append(
            (
                [e.type for e in result.events],
                [e.payload.get("code") for e in result.events if e.type == "notice"],
            )
        )
    assert [t.count("card") for t, _ in outcomes] == [1, 1, 0]
    assert outcomes[2][1] == ["back_edge_limit"]


async def test_keeping_the_value_after_a_back_edge_changes_nothing(service) -> None:
    conv = await _review(service)
    before = await conv.brief()
    result = await service.reopen_slot(
        conv.caller, conv.session_id, "idea.business_model", "a surprise"
    )
    conv.events.extend(result.events)
    keep = next(o for o in conv.last_card().options if not o.sets)
    await conv.answer(keep.id)
    assert (await conv.brief())["_hash"] == before["_hash"]


async def test_the_back_edge_refuses_an_unknown_slot_and_a_busy_session(service) -> None:
    conv = await _review(service)
    with pytest.raises(NotFound):
        await service.reopen_slot(conv.caller, conv.session_id, "no.such_slot", "x")
    with pytest.raises(NotFound):
        await service.reopen_slot(conv.caller, "ses_missing", "idea.business_model", "x")
