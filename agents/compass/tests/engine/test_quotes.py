from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_compass.domain.cards import Card, CardOption
from mde_compass.engine.quotes import (
    normalize,
    option_label_sources,
    sources_for_text,
    verify_against,
    verify_quote,
)

SOURCE = "I want an app for my tiffin service in Pune. Customers pay weekly."


def test_normalize_basics() -> None:
    assert normalize("  Hello \t\n  WORLD  ") == "hello world"
    assert normalize("ＡＢＣ　ｄｅｆ") == "abc def"  # NFKC: fullwidth letters and ideographic space
    assert normalize("") == "" and normalize("   \n ") == ""


def test_normalize_folds_typography() -> None:
    assert normalize("it’s “fine” – really") == 'it\'s "fine" - really'
    assert normalize("wait…") == "wait..."


def test_normalize_drops_zero_width_and_controls() -> None:
    assert normalize("ti​ffin‌ ser‍vice﻿") == "tiffin service"
    assert normalize("a­b\x00c") == "abc"


@given(st.text(max_size=200))
def test_normalize_is_idempotent(text: str) -> None:
    once = normalize(text)
    assert normalize(once) == once
    assert once == once.strip() and "  " not in once


# ------------------------------------------------------------------------------------------ verify_quote
def test_exact_and_case_and_space_variants_verify() -> None:
    assert verify_quote("tiffin service in Pune", SOURCE)
    assert verify_quote("TIFFIN   SERVICE\nIN pune", SOURCE)
    assert verify_quote("  customers pay weekly.  ", SOURCE)


def test_near_miss_is_rejected() -> None:
    assert not verify_quote("tiffin services in Pune", SOURCE)  # one letter more
    assert not verify_quote("tiffin service in Mumbai", SOURCE)  # a changed place
    assert not verify_quote("customers pay monthly", SOURCE)  # a changed fact
    assert not verify_quote("customers weekly pay", SOURCE)  # word order


def test_ellipsis_or_paraphrase_is_rejected() -> None:
    assert not verify_quote("tiffin service ... customers pay weekly", SOURCE)
    assert not verify_quote("people pay every week for lunch boxes", SOURCE)


def test_short_and_empty_quotes_are_rejected() -> None:
    assert not verify_quote("", SOURCE)
    assert not verify_quote("   ", SOURCE)
    assert not verify_quote("ap", SOURCE)
    assert verify_quote("app", SOURCE)
    assert not verify_quote("app", SOURCE, min_chars=4)
    assert not verify_quote("tiffin", "")  # empty source


def test_zero_width_characters_do_not_hide_or_fake_a_match() -> None:
    assert verify_quote("tif​fin ser‌vice", SOURCE)  # junk inside the quote
    assert verify_quote("tiffin service", "I run a tif​fin ser‍vice")  # junk inside the source
    assert not verify_quote("tiffin​ service!!", "tiffin kitchen")  # junk never makes a match


def test_wrapping_quote_marks_and_edge_punctuation_are_not_content() -> None:
    assert verify_quote('"tiffin service in Pune"', SOURCE)
    assert verify_quote("“tiffin service in Pune”,", SOURCE)
    assert verify_quote("tiffin service in Pune.", "my tiffin service in Pune and more")
    assert not verify_quote('"..."', SOURCE)  # nothing left once the noise is stripped


def test_typographic_apostrophes_match() -> None:
    assert verify_quote("we don't deliver", "We don’t deliver on Sundays")


def test_devanagari_quotes() -> None:
    source = "मुझे पुणे में टिफिन सर्विस का ऐप चाहिए। ग्राहक हर हफ्ते पैसे देते हैं।"
    assert verify_quote("पुणे में टिफिन सर्विस", source)
    assert verify_quote("ग्राहक हर हफ्ते पैसे देते हैं।", source)
    assert not verify_quote("पुणे में टिफिन सेवा", source)
    assert not verify_quote("मुंबई में टिफिन", source)


def test_devanagari_nukta_forms_compare_equal() -> None:
    precomposed = "क़"  # क़ as one code point
    decomposed = "क़"  # क + nukta
    assert normalize(precomposed) == normalize(decomposed)
    assert verify_quote(f"{precomposed}ीमत कम", f"मुझे {decomposed}ीमत कम चाहिए")


def test_hinglish_quotes() -> None:
    source = "Mujhe Pune mein tiffin delivery ka app chahiye, customers weekly paise dete hain"
    assert verify_quote("tiffin delivery ka app chahiye", source)
    assert verify_quote("CUSTOMERS  weekly paise dete hain", source)
    assert not verify_quote("tiffin delivery ka app banao", source)


def test_mixed_script_quote_must_match_both_halves() -> None:
    source = "Mera business है tiffin delivery का"
    assert verify_quote("business है tiffin", source)
    assert not verify_quote("business hai tiffin", source)


def test_verify_against_multiple_sources() -> None:
    sources = ["Weekly plan", "Daily meal"]
    assert verify_against("daily meal", sources)
    assert not verify_against("monthly plan", sources)
    assert not verify_against("daily meal", [])


# --------------------------------------------------------------------------- option label sources
def _card() -> Card:
    return Card(
        id="Q-001",
        frame="planner",
        slots=[],
        type="choose_one",
        question="Should customers pick a meal each day or subscribe weekly?",
        why_we_ask="This decides the ordering flow.",
        options=[
            CardOption(id="a", label="Pick each day", description="They order daily", reason="r"),
            CardOption(id="b", label="Weekly plan"),
            CardOption(id="c", label="Both"),
        ],
    )


def test_tap_source_is_the_selected_labels_only() -> None:
    card = _card()
    assert option_label_sources(card, ["b"]) == ["Weekly plan"]
    assert option_label_sources(card, ["c", "a"]) == ["Both", "Pick each day"]


def test_unknown_and_repeated_ids_are_ignored() -> None:
    card = _card()
    assert option_label_sources(card, ["zzz", "a", "a"]) == ["Pick each day"]
    assert option_label_sources(card, []) == []


def test_question_text_is_never_a_source() -> None:
    card = _card()
    sources = option_label_sources(card, ["a", "b", "c"])
    joined = " ".join(sources)
    assert card.question not in sources and "subscribe weekly" not in joined
    # A "quote" copied from the assistant's question does not verify against the tap.
    assert not verify_against("subscribe weekly", sources)
    assert not verify_against(
        "They order daily", sources
    )  # the option description is not a source either
    assert verify_against("weekly plan", sources)


def test_sources_for_text_drops_blanks() -> None:
    assert list(sources_for_text("a", None, "  ", "b")) == ["a", "b"]


@pytest.mark.parametrize("quote", ["tiffin", "Tiffin Service", "pune", "paY weekly"])
def test_case_insensitive_parametrized(quote: str) -> None:
    assert verify_quote(quote, SOURCE)
