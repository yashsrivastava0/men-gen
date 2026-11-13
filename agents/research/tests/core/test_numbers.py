"""RV-18 building blocks: figures compare across separators, currencies, scripts, number words and dates."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.core.numbers import (
    claim_figures,
    data_figure_texts,
    figures,
    missing_figures,
    to_ascii_digits,
)


def keys(text: str, **kw: bool) -> set[str]:
    return {f.key for f in figures(text, **kw)}


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of ₹5,000", "the fee is 5000 rupees"),
        ("Rs. 5000 per month", "Rs 5,000.00 a month"),
        ("2.5% per transaction", "charges 2.5 % on each transaction"),
        ("2.5 percent", "2.5 per cent of the amount"),
        ("settles in 10-15 days", "settlement takes 10 to 15 business days"),
        ("1,23,456 rupees", "a limit of 123456 rupees"),
        ("1.234,56 euro", "an amount of 1234.56 euro"),
        ("a limit of 5 000 rupees", "limit: 5000 rupees"),
        ("two documents are needed", "you need 2 documents"),
        ("needs 3 documents", "needs three documents"),
        ("Rs. 1.2 lakh limit", "limit of Rs. 1.2 lakh per day"),
        ("valid until 12 March 2026", "valid until March 12, 2026"),
        ("updated March 2026", "last updated on 12 March 2026"),
        ("updated 2026-03", "Updated 2026-03-12"),
        (
            "fee ₹५,०००",
            "the fee is ₹5000",
        ),  # Devanagari digits (the claim names a currency, so the quote must too)
        ("fee ٥٠٠٠", "the fee is 5000"),  # Arabic-Indic digits
        ("２.５％ fee", "the fee is 2.5%"),  # full-width forms
    ],
)
def test_the_same_figure_written_two_ways_is_one_figure(claim: str, quote: str) -> None:
    assert missing_figures(figures(claim, claim_side=True), figures(quote)) == []


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of 5,000", "fee of 500"),
        ("2.5%", "2%"),
        ("10-15 days", "10 days"),
        ("1.2 lakh", "120,000"),  # lakh is kept as written: the quote must say it the same way
        ("12 March 2026", "March 2026"),  # a day the quote does not give
        ("March 2026", "April 2026"),
        ("takes three days", "takes a few days"),
    ],
)
def test_a_figure_the_quote_does_not_contain_is_reported(claim: str, quote: str) -> None:
    assert missing_figures(figures(claim, claim_side=True), figures(quote))


def test_the_word_one_is_not_demanded_of_a_quote_but_does_satisfy_a_digit() -> None:
    assert keys("one of the best", claim_side=True) == set()
    assert "n:1" in keys("one of the best")
    assert missing_figures(figures("1 step", claim_side=True), figures("a one step flow")) == []


def test_list_commas_are_not_thousands() -> None:
    assert keys("steps 1, 2, 3") == {"n:1", "n:2", "n:3"}
    assert keys("1,000 and 2,50,000") == {"n:1000", "n:250000"}


def test_a_date_does_not_also_count_as_loose_numbers() -> None:
    assert keys("on 12 March 2026", claim_side=True) == {"d:2026-03-12"}


def test_data_figures_come_from_numeric_fields_and_marked_strings_only() -> None:
    data = {
        "provider": "Acme 24x7",
        "value": "2.5%",
        "unit": "per transaction",
        "fees": "Rs. 3 per payout",
        "steps": ["Open the app 2 times", "Pay ₹100"],
        "limits": "10 per day",
    }
    texts = data_figure_texts(data)
    assert (
        "2.5%" in texts
        and "Rs. 3 per payout" in texts
        and "Pay ₹100" in texts
        and "10 per day" in texts
    )
    assert "Acme 24x7" not in texts and "Open the app 2 times" not in texts


def test_claim_figures_combine_claim_label_and_numeric_data() -> None:
    figs = {f.key for f in claim_figures("settles in 3 days", "Fee 2%", {"value": "1.5"})}
    assert figs == {"n:3", "n:2", "n:1.5"}


def test_ascii_digits() -> None:
    assert to_ascii_digits("५,०००") == "5,000"


@given(st.integers(min_value=0, max_value=10**9))
def test_an_integer_matches_itself_in_every_grouping(n: int) -> None:
    plain = str(n)
    western = f"{n:,}"
    assert missing_figures(figures(plain), figures(western)) == []
    assert missing_figures(figures(western), figures(plain)) == []


@given(st.text(max_size=200))
def test_figures_never_raise_and_are_deterministic(text: str) -> None:
    assert figures(text) == figures(text)
    assert figures(text, claim_side=True) == figures(text, claim_side=True)


def test_a_number_longer_than_the_decimal_precision_does_not_raise() -> None:
    """Found by the property test above on 2026-11-11: quantize() raised for 29 digits, which would have crashed a topic."""
    for digits in (28, 29, 40, 400):
        assert figures("1" + "0" * digits)
