"""Language detection: scripts, Hinglish markers, keeping the previous language, linear time."""

from __future__ import annotations

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_compass.engine.language import detect_language, devanagari_share, normalize


@pytest.mark.parametrize(
    ("text", "previous", "expected"),
    [
        ("I want an app for my bakery", "en", "en"),
        ("मुझे अपनी दुकान के लिए ऐप चाहिए", "en", "hi"),
        ("mujhe ek app chahiye", "en", "hi"),
        ("mera business hai kapde ka", "en", "hi"),
        ("ok", "hi", "hi"),  # a short word never flips the chat language
        ("ok", "en", "en"),
        ("Pune", "hi", "hi"),
        ("kya", "en", "hi"),  # one strong marker in a very short message
        ("I want to build a shop for my products", "hi", "en"),  # a clear switch back (IV-09)
        ("hello there", "hi", "hi"),  # not enough evidence to switch
        ("", "hi", "hi"),
        ("12345 !!!", "en", "en"),
        ("मेरी shop है और I sell cakes", "en", "hi"),  # mixed: Devanagari share is enough
    ],
)
def test_detect_language(text: str, previous: str, expected: str) -> None:
    assert detect_language(text, previous) == expected


def test_unknown_previous_falls_back_to_english() -> None:
    assert detect_language("ok", "fr") == "en"


def test_one_marker_in_a_long_english_sentence_is_not_hindi() -> None:
    assert (
        detect_language("I would like the app to show the kya status page for each order", "en")
        == "en"
    )


@given(st.text(max_size=300), st.sampled_from(["en", "hi"]))
def test_always_en_or_hi(text: str, previous: str) -> None:
    assert detect_language(text, previous) in ("en", "hi")


def test_linear_on_hostile_input() -> None:
    for text in ("a" * 200_000, "mujhe " * 40_000, "ह" * 200_000, "​" * 200_000):
        start = time.perf_counter()
        detect_language(text)
        assert time.perf_counter() - start < 1.0


def test_devanagari_share_and_normalize() -> None:
    assert devanagari_share("abc") == 0.0
    assert devanagari_share("") == 0.0
    assert devanagari_share("हिन्दी") == 1.0
    assert normalize("  Don't  STOP!! ") == "dont stop"
