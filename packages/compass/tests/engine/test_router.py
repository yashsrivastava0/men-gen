"""The keyword router: taps and obvious commands never cost a model call."""

from __future__ import annotations

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_compass.engine.router import load_phrases, route


@pytest.mark.parametrize(
    ("text", "pending", "kind"),
    [
        ("hi", False, "greeting"),
        ("Hello!", False, "greeting"),
        ("नमस्ते", False, "greeting"),
        ("namaste", False, "greeting"),
        ("you decide", True, "you_decide"),
        ("Please, you decide for me", True, "you_decide"),
        ("aap decide kar lo", True, "you_decide"),
        ("आप तय कर लो", True, "you_decide"),
        ("finish with smart defaults", True, "finish_with_defaults"),
        ("please use defaults for the rest", False, "finish_with_defaults"),
        ("baaki sab default rakho", False, "finish_with_defaults"),
        ("stop", False, "stop"),
        ("रुकिए", False, "stop"),
        ("pause", True, "pause"),
        ("one moment", True, "pause"),
        ("start over", False, "start_over"),
        ("shuru se karo", False, "start_over"),
        ("none of these", True, "none_of_these"),
        ("show me more", True, "none_of_these"),
        ("back", True, "back"),
        ("go back", False, "back"),
        ("undo", True, "undo"),
        ("undo that", False, "undo"),
        ("what do you know so far", False, "show_what_you_know"),
        ("show my brief", False, "show_what_you_know"),
    ],
)
def test_routes(text: str, pending: bool, kind: str) -> None:
    result = route(text, has_pending_card=pending, language="en")
    assert result is not None
    assert result.kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "I want an app for my restaurant in Pune",
        "the delivery should stop at the door",  # contains "stop" but is content
        "can you go back to the pricing question and also add reviews and ratings for all of the products",
        "",
        "   ",
        "hello I need a booking app for my salon",
    ],
)
def test_content_goes_to_the_understander(text: str) -> None:
    assert route(text, has_pending_card=True, language="en") is None


def test_card_bound_commands_need_a_pending_card() -> None:
    assert route("you decide", has_pending_card=False, language="en") is None
    assert route("none of these", has_pending_card=False, language="en") is None


def test_tables_are_loaded_once_and_cover_three_languages() -> None:
    ph = load_phrases()
    assert load_phrases() is ph
    assert any(ord(c) > 0x900 for p in ph.intents["greeting"] for c in p)  # Hindi
    assert "tum decide karo" in ph.intents["you_decide"]  # Hinglish


@given(st.text(max_size=200))
def test_route_never_raises(text: str) -> None:
    route(text, has_pending_card=True, language="hi")


def test_linear_on_hostile_input() -> None:
    for text in ("stop " * 40_000, "a" * 200_000, "you decide " * 20_000):
        start = time.perf_counter()
        route(text, has_pending_card=True, language="en")
        assert time.perf_counter() - start < 1.0
