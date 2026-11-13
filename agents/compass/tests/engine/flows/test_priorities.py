"""The forced first-release ranking: cue detection, when it is due, the card, and placing the answer."""

from __future__ import annotations

import time

import pytest

from mde_compass.catalog import load_catalog
from mde_compass.engine.flows import data, priorities

FEATURES = [
    {"id": f"FTR-00{i}", "name": n, "intent": f"does {n}", "_meta": {"status": "confirmed"}}
    for i, n in enumerate(["Order", "Pay", "Track", "Chat", "Reviews", "Offers"], start=1)
]


@pytest.mark.parametrize(
    "text",
    [
        "Everything is a must.",
        "everything is a must have, nothing can wait",
        "honestly all of it is a must",
        "sab zaroori hai",
        "Sab kuch zaroori hai bhai",
        "मुझे सब ज़रूरी चाहिए",
        "every feature is a must for us",
    ],
)
def test_the_everything_is_a_must_cue_is_found_in_three_languages(text: str) -> None:
    assert priorities.everything_is_must(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not everything is a must, just ordering",
        "I want a food delivery app in Pune",
        "sab zaroori nahi hai",
        "nothing is a must yet",
    ],
)
def test_other_text_is_not_the_cue(text: str) -> None:
    assert priorities.everything_is_must(text) is None


def test_the_cue_matcher_is_linear_on_hostile_text() -> None:
    hostile = ("everything is a " * 3000) + "m" * 5000
    started = time.perf_counter()
    priorities.everything_is_must(hostile)
    priorities.everything_is_must("a " * 30_000)
    assert time.perf_counter() - started < 2.0


def _due(**over: object) -> bool:
    args: dict[str, object] = {
        "features": FEATURES,
        "scope_priority": "must",
        "scope_priority_status": "volunteered",
        "asked_frames": ["features"],
    }
    args.update(over)
    return priorities.is_due(**args)  # type: ignore[arg-type]


def test_due_only_after_the_user_said_everything_is_a_must() -> None:
    assert _due()
    assert not _due(scope_priority=None, scope_priority_status="empty")
    assert not _due(scope_priority="nice")


def test_due_once_and_only_with_something_to_rank() -> None:
    assert not _due(asked_frames=["features", "priorities"])
    assert not _due(scope_priority_status="confirmed")
    assert not _due(features=FEATURES[:1])
    assert not _due(features=None)


def test_features_already_later_are_not_ranked_again() -> None:
    later = [
        {**FEATURES[0], "priority": "later"},
        {**FEATURES[1], "scope_status": "later"},
        *FEATURES[2:],
    ]
    assert [f["name"] for f in priorities.rankable(later)] == ["Track", "Chat", "Reviews", "Offers"]


def test_the_card_holds_the_users_own_features_and_cannot_be_skipped() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    assert (card.frame, card.type, card.slots) == ("priorities", "priority", ["scope.priority"])
    assert [o.label for o in card.options] == [f["name"] for f in FEATURES]
    assert "five" in card.question
    assert card.must_confirm and not card.skip.enabled and not card.you_decide.enabled
    assert priorities.flow_card(card)
    hi = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="hi"
    )
    assert "पाँच" in hi.question


def test_with_five_or_fewer_the_question_is_the_plain_one() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES[:3], catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    assert "five" not in card.question and "first version" in card.question


def test_a_ranked_answer_makes_the_first_five_must_and_the_rest_later() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    placed = priorities.place(card, ["F2", "F1", "F3", "F4", "F5", "F6"])
    assert placed.names("must") == ["Order", "Pay", "Track", "Chat", "Reviews"]
    assert placed.names("later") == ["Offers"]
    assert priorities.place(card, ["F6"]).names("must") == [
        "Offers"
    ]  # picked three or fewer: just those


def test_a_bucket_answer_is_taken_as_given() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    placed = priorities.place(card, {"must": ["F1", "F2"], "nice": ["F3"], "later": ["F4"]})
    assert placed.priorities["Order"] == "must"
    assert placed.priorities["Track"] == "nice"
    assert placed.priorities["Chat"] == "later"
    assert placed.priorities["Offers"] == "later"  # not placed: waits


def test_junk_answers_place_nothing_as_must() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    for junk in (None, "zzz", 7, True, ["nope"], {"must": "F1"}):
        assert priorities.place(card, junk).names("must") == []


def test_applying_a_placement_keeps_ids_and_sets_status_for_later() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    placed = priorities.place(card, ["F1", "F2"])
    out = priorities.apply_placement([*FEATURES, "stray", {"nameless": 1}], placed)
    by_name = {i["name"]: i for i in out if isinstance(i, dict) and "name" in i}
    assert by_name["Order"]["priority"] == "must" and by_name["Order"]["id"] == "FTR-001"
    assert by_name["Offers"]["priority"] == "later" and by_name["Offers"]["scope_status"] == "later"
    assert "_meta" not in by_name["Order"]  # the confirmed answer's own meta applies now
    assert "stray" in out


def test_tick_all_ticks_every_option_with_evidence_not_suggestion() -> None:
    cat = load_catalog()
    card = priorities.build_card(
        FEATURES, catalog=cat, card_id="Q-009", n=4, of_about=8, language="en"
    )
    ticked = priorities.tick_all(card, "You said everything is a must")
    assert all(o.ticked and o.basis != "suggestion" for o in ticked.options)


def test_the_policy_comes_from_the_data_file() -> None:
    assert data.policy().first_release_max == 5
    assert data.number_word(5, "en") == "five" and data.number_word(99, "en") == "99"
