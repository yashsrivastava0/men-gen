import random
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_research_agent.core.quotes import (
    normalize_text,
    normalize_with_offsets,
    verify_quote,
)

PAGE_EN = (
    "Our platform supports UPI, cards and net banking. Settlement happens in T+1 business days "
    "for domestic payments — international payments take up to 5 days.\n\n"
    "The “instant payout” option is available on request."
)
PAGE_HI = "भुगतान का नियम: इनवॉइस में जीएसटीआईएन अनिवार्य है।"
PAGE_KN = "ಸೆಮೆಸ್ಟರ್ ಶುಲ್ಕ ದುಬಾರಿ ವಿಶೇಷ ಸ್ಕೀಮ್ ಲಭ್ಯವಿದೆ"
PAGE_AR = "يدعم النظام الدفع عبر البطاقات والتحويل البنكي المباشر"


def ok(page: str, quote: str) -> bool:
    return verify_quote(page, quote).ok


def test_exact_substring_verifies_and_gives_the_original_span() -> None:
    q = "Settlement happens in T+1 business days"
    res = verify_quote(PAGE_EN, q)
    assert res.ok and PAGE_EN[res.start : res.end] == q and res.fragments == 1


@pytest.mark.parametrize(
    "quote",
    [
        "settlement HAPPENS in t+1 business days",  # case
        "Settlement  happens\tin T+1\nbusiness days",  # whitespace
        "Settlement happens in T+1 business days for domestic payments - international payments",  # dash
        'The "instant payout" option is available on request',  # straight vs curly quotes
        "The “instant payout” option is available",
        "Our platform supports UPI, cards and net banking",  # NBSP
        "Our plat​form supports UPI, cards and net banking",  # zero-width space
        "Our platform supports \ufeffUPI, ca\u00adrds and net banking",  # BOM, soft hyphen inside a word
    ],
)
def test_invisible_and_typographic_differences_still_verify(quote: str) -> None:
    assert ok(PAGE_EN, quote), quote


def test_a_changed_word_does_not_verify() -> None:
    assert not ok(PAGE_EN, "Settlement happens in T+2 business days")
    assert verify_quote(PAGE_EN, "Settlement happens in T+2 business days").problem == "not_found"
    assert not ok(PAGE_EN, "Our platform supports UPI, cards and net-banking wallets")


def test_too_short_and_empty_quotes_are_refused() -> None:
    assert verify_quote(PAGE_EN, "").problem == "empty"
    assert verify_quote(PAGE_EN, " ​ ").problem == "empty"
    assert verify_quote(PAGE_EN, "UPI").problem == "too_short"
    assert verify_quote(PAGE_EN, "T+1 days").problem == "too_short"  # 8 chars


def test_ellipsis_stitched_quotes_check_each_fragment_in_order() -> None:
    assert ok(
        PAGE_EN,
        "Settlement happens in T+1 business days ... international payments take up to 5 days",
    )
    assert ok(
        PAGE_EN, "Settlement happens in T+1 business days… international payments take up to 5 days"
    )
    assert not ok(
        PAGE_EN, "international payments take up to 5 days ... Settlement happens in T+1"
    )  # wrong order
    assert not ok(
        PAGE_EN, "Settlement happens in T+1 business days ... hidden fragment not on the page"
    )
    res = verify_quote(PAGE_EN, "Our platform supports UPI ... instant payout")
    assert res.fragments == 2 and res.ok
    assert (
        verify_quote(PAGE_EN, "Settlement happens ... ok").problem == "too_short"
    )  # tiny fragment


def test_devanagari_kannada_arabic_quotes() -> None:
    assert ok(PAGE_HI, "इनवॉइस में जीएसटीआईएन अनिवार्य है")
    assert ok(PAGE_KN, "ಶುಲ್ಕ ದುಬಾರಿ ವಿಶೇಷ ಸ್ಕೀಮ್")
    assert ok(PAGE_AR, "الدفع عبر البطاقات والتحويل البنكي")


def test_zero_width_joiners_in_indic_text_do_not_break_matching() -> None:
    quote = "इनवॉइस में जीएसटीआईएन अनिवार्य है"
    with_zwnj = quote.replace("र्य", "र्‌य")  # ZWNJ after the virama
    assert ok(PAGE_HI, with_zwnj)
    assert ok(PAGE_HI.replace("र्य", "र्‍य"), quote)  # ZWJ in the page


def test_changed_indic_vowel_sign_is_a_different_word() -> None:
    quote = "इनवॉइस में जीएसटीआईएन अनिवार्य है"
    assert not ok(PAGE_HI, quote.replace("े", "ै", 1))  # e-matra -> ai-matra


def test_composed_and_decomposed_accents_match() -> None:
    page = "The café menu lists crème brûlée daily"
    decomposed = unicodedata.normalize("NFD", "the café menu lists crème brûlée daily")
    assert ok(page, decomposed)
    assert ok(unicodedata.normalize("NFD", page), "the café menu lists crème brûlée")


def test_ligatures_and_fullwidth_letters_fold() -> None:
    assert ok(
        "The oﬃce ﬁle is ready for the ＡＭＭ team", "The office file is ready for the AMM team"
    )


def test_normal_form_is_idempotent_on_awkward_text() -> None:
    for s in (PAGE_EN, PAGE_HI, PAGE_KN, PAGE_AR, "  A​  B ‘x’ — … ﬁ  "):
        once = normalize_text(s)
        assert normalize_text(once) == once


def test_offsets_point_back_into_the_original() -> None:
    page = "AAA   “BBB” CCC"
    n = normalize_with_offsets(page)
    assert n.text == 'aaa "bbb" ccc'
    s, e = n.original_span(n.text.index("bbb"), n.text.index("bbb") + 3)
    assert page[s:e] == "BBB"
    assert len(n.starts) == len(n.ends) == len(n.text)
    assert all(0 <= a < b <= len(page) for a, b in zip(n.starts, n.ends, strict=True))


_WORDS = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=2, max_size=9
    ),
    min_size=6,
    max_size=14,
)


def test_a_letter_whose_case_fold_decomposes_matches_its_upper_case_form() -> None:
    # Greek iota with dialytika and tonos: folds to three characters, while the upper-case form folds to two
    page = "00 00000ΐ 00 00 00 00"
    quote = "00000­Ϊ́\n00\n00\n00"
    assert verify_quote(page, quote).ok
    assert normalize_text("ΐ") == normalize_text("Ϊ́")


@settings(max_examples=60, deadline=None)
@given(_WORDS, st.integers(0, 10_000))
def test_perturbing_a_real_substring_keeps_it_verified(words: list[str], seed: int) -> None:
    rng = random.Random(seed)
    page = " ".join(words)
    i = rng.randrange(0, max(1, len(words) - 4))
    quote_words = words[i : i + 4]
    quote = " ".join(quote_words)
    if len(normalize_text(quote)) < 12:
        return
    noisy = []
    for w in quote_words:
        w = rng.choice(
            [v for v in (w, w.upper(), w.lower()) if v.casefold() == w.casefold()]
        )  # not Turkish dotless i
        if len(w) > 2 and rng.random() < 0.5:
            cut = rng.randrange(1, len(w))
            w = w[:cut] + rng.choice(["​", "‌", "﻿", "­"]) + w[cut:]
        noisy.append(w)
    assert verify_quote(page, rng.choice([" ", " ", "\n", "\t"]).join(noisy)).ok


@settings(max_examples=60, deadline=None)
@given(_WORDS)
def test_a_word_not_on_the_page_never_verifies(words: list[str]) -> None:
    page = " ".join(words)
    quote = " ".join(words[:4]) + " zzqxjv"
    if "zzqxjv" in page.lower():
        return
    assert not verify_quote(page, quote).ok


def test_large_page_is_fast_enough() -> None:
    page = (
        "Typical paragraph about payments and settlement windows. " * 8000
    ) + "The needle sentence is here once."
    assert verify_quote(page, "The needle sentence is here once.").ok
