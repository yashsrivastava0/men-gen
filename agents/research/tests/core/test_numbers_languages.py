"""RV-18 across languages and currencies: decimal commas, space and dot thousands, currency tokens, typed values.

Each language is a table of ``(claim, quote)`` pairs the check must accept and pairs it must reject. Claims are English (the
engine writes claims in English with source-language quotes); the quotes come from pt-PT, fr-SN, es-MX, es-GT, es-CR, en-IN
and hi pages."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.core.currencies import agree, codes_of, mentions
from mde_research_agent.core.numbers import (
    claim_figures,
    data_figure_texts,
    figures,
    missing_figures,
    strip_unverified,
)


def keys(text: str, **kw: bool) -> list[str]:
    return sorted(f.key for f in figures(text, **kw))


def num_keys(text: str, **kw: bool) -> list[str]:
    return [k for k in keys(text, **kw) if k.startswith("n:")]


def backed(claim: str, quote: str) -> bool:
    return missing_figures(figures(claim, claim_side=True), figures(quote)) == []


# --- decimal comma ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12,50 EUR", ["n:12.5"]),
        ("2,5%", ["n:2.5"]),
        ("taxa de 0,8 % por transação", ["n:0.8"]),
        ("frais de 1,5 % par paiement", ["n:1.5"]),
        ("comisión del 3,5 % más 4,00 pesos", ["n:3.5", "n:4"]),
        ("3,14159", ["n:3.14159"]),
        ("1,234", ["n:1234"]),  # three digits after the comma: a thousands comma, as before
        ("5,000", ["n:5000"]),
        ("1,23,456", ["n:123456"]),
        ("1,2,3", ["n:1", "n:2", "n:3"]),
        ("steps 1, 2, 3", ["n:1", "n:2", "n:3"]),
        ("1.234,56 EUR", ["n:1234.56"]),
        ("1.234.567", ["n:1234567"]),
        ("1.234", ["n:1.234"]),  # one dot group: a decimal, as before
        ("0,05", ["n:0.05"]),
    ],
)
def test_decimal_commas_and_thousands(text: str, expected: list[str]) -> None:
    assert num_keys(text) == sorted(expected)


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of 12.50 EUR", "Frais : 12,50 EUR par virement"),
        ("2.5% per transaction", "taxa de 2,5% por transação"),
        ("0.8% commission", "comisión de 0,8 % sobre cada venta"),
        ("limit of 1,234.56 EUR", "limite de 1 234,56 EUR"),
        ("limit of 1234567 XOF", "plafond de 1 234 567 XOF"),
        ("limit of 1234567 EUR", "límite de 1.234.567 EUR"),
        ("limit of 5000 EUR", "limite de 5 000 EUR"),
        ("limit of 5000 EUR", "limite de 5 000 EUR"),  # no-break space
        ("limit of 5000 EUR", "limite de 5 000 EUR"),  # thin space
        ("limit of 5000 EUR", "limite de 5 000 EUR"),  # narrow no-break space
        ("fee of 12.5 EUR", "fee of 12.50 EUR"),
    ],
)
def test_the_same_amount_written_the_local_way_is_one_figure(claim: str, quote: str) -> None:
    assert backed(claim, quote), (claim, quote)


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of 12.50 EUR", "Frais : 1 250 EUR"),
        ("2.5% fee", "taxa de 25%"),
        ("2.5% fee", "taxa de 5 e 25%"),
        ("limit of 5000 EUR", "limite de 50 000 EUR"),
        ("limit of 5000 EUR", "limite de 5,50 EUR"),
    ],
)
def test_a_different_amount_is_still_missing(claim: str, quote: str) -> None:
    assert not backed(claim, quote)


# --- plain space between digits ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 000", ["n:5000"]),
        ("1 234 567", ["n:1234567"]),
        ("10 000 000", ["n:10000000"]),
        (
            "2024 500",
            ["n:2024", "n:500"],
        ),  # the bug: a four-digit leading group is not a thousands group
        ("2 0245", ["n:2", "n:245"]),
        ("1 23", ["n:1", "n:23"]),
        ("12 34 56", ["n:12", "n:34", "n:56"]),
        ("in 2026 1 500 people", ["n:1500", "n:2026"]),
        ("10 20", ["n:10", "n:20"]),
        ("5 000,50", ["n:5000.5"]),
    ],
)
def test_space_thousands_join_only_groups_of_exactly_three_digits(
    text: str, expected: list[str]
) -> None:
    assert num_keys(text) == sorted(expected)


def test_a_year_followed_by_a_number_is_not_a_thousands_number() -> None:
    assert "n:2024500" not in num_keys("valid from 2024 500 pages")
    assert not backed("2024500 pages", "valid from 2024 500 pages")
    assert backed("2024 and 500", "valid from 2024 500 pages")


# --- currencies ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("$350", {"USD", "MXN"}),
        ("₹350", {"INR"}),
        ("Rs. 350", {"INR"}),
        ("350 EUR", {"EUR"}),
        ("€ 350", {"EUR"}),
        ("350 euros", {"EUR"}),
        ("5 000 FCFA", {"XOF", "XAF"}),
        ("5 000 XOF", {"XOF"}),
        ("5 000 francs CFA", {"XOF", "XAF"}),
        ("MXN 350", {"MXN"}),
        ("350 pesos", {"MXN"}),
        ("Q 350 GTQ", {"GTQ"}),
        ("₡5000", {"CRC"}),
        ("5.000 colones", {"CRC"}),
        ("350 BRL", {"BRL"}),
        ("R$ 350", {"BRL"}),
        ("£350", {"GBP"}),
        ("350 GBP", {"GBP"}),
        ("350 USD", {"USD"}),
    ],
)
def test_a_currency_next_to_an_amount_is_read(text: str, codes: set[str]) -> None:
    found = [m for m in mentions(text) if codes & set(m.codes)]
    assert found, (text, [m.codes for m in mentions(text)])


@pytest.mark.parametrize(
    "text",
    [
        "pay in euros",
        "the dollar is strong",
        "Rs",
        "Q3 2026 plan",
        "the colon is a mark",
        "price: n/a",
    ],
)
def test_a_currency_word_with_no_amount_beside_it_is_not_a_figure(text: str) -> None:
    assert not [f for f in figures(text) if f.key.startswith("c:")]


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of ₹350", "the fee is ₹350"),
        ("fee of ₹350", "the fee is Rs. 350"),
        ("fee of 350 INR", "Fee: Rs 350"),
        ("fee of $350", "the fee is $350"),
        ("fee of 350 MXN", "la cuota es de $350"),  # a bare $ can be pesos
        ("fee of 350 USD", "the fee is $350"),
        ("fee of 5000 XOF", "frais de 5 000 FCFA"),  # FCFA may be XOF
        ("fee of 5000 XAF", "frais de 5 000 FCFA"),
        ("fee of 12.50 EUR", "frais de 12,50 euros"),
        ("fee of €12.50", "frais : 12,50 EUR"),
        ("fee of 500 CRC", "cuota de ₡500"),
        ("fee of 500 GTQ", "cuota de 500 quetzales"),
        ("fee of 500 BRL", "taxa de R$ 500"),
    ],
)
def test_a_claim_currency_backed_by_the_quote_passes(claim: str, quote: str) -> None:
    assert backed(claim, quote), (claim, quote)


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("fee of $350", "the fee is ₹350"),  # the swap the old check let through
        ("fee of ₹350", "the fee is $350"),
        ("fee of 350 EUR", "the fee is 350 USD"),
        ("fee of 350 MXN", "the fee is 350 USD"),
        ("fee of 350 USD", "la cuota es de 350 pesos"),
        ("fee of 5000 XOF", "fee of 5 000 EUR"),
        ("fee of 500 GTQ", "cuota de ₡500"),
        ("fee of 350 INR", "the fee is 350"),  # the claim names a currency, the quote none
        ("fee of 12.50 EUR", "frais de 12,50"),
    ],
)
def test_a_currency_the_quote_does_not_state_is_a_currency_mismatch(claim: str, quote: str) -> None:
    missing = missing_figures(figures(claim, claim_side=True), figures(quote))
    assert missing and all(f.key.startswith("c:") for f in missing), (claim, quote, missing)


def test_a_claim_with_no_currency_is_not_held_to_the_quotes_currency() -> None:
    assert backed("a fee of 350", "the fee is ₹350")
    assert backed("2.5% per transaction", "2,5 % par transaction")


def test_currency_codes_agree_by_overlap() -> None:
    assert agree(codes_of("$"), [codes_of("MXN")])
    assert agree(codes_of("MXN"), [codes_of("$")])
    assert not agree(codes_of("USD"), [codes_of("EUR")])
    assert codes_of("Rs.") == codes_of("rs") and "INR" in codes_of("Rs.")
    assert codes_of("unknown") == frozenset()


# --- typed values ------------------------------------------------------------------------------------------------------------


def test_typed_value_keys_are_numeric_fields() -> None:
    data = {
        "provider": "Acme",
        "fee": {"amount": "350", "currency": "INR", "period": "per month", "unit": "per user"},
        "limit": {"amount": Decimal("5000.50"), "currency": "EUR", "unit": "per day"},
    }
    texts = data_figure_texts(data)
    assert "350" in texts and "5000.50" in texts and "INR" in texts and "per month" in texts
    assert "Acme" not in texts


def test_a_typed_amount_cannot_bypass_rv18() -> None:
    data = {"fee": {"amount": "350", "currency": "INR", "period": "per month"}}
    needed = claim_figures("Monthly fee", "Fee", data)
    assert {"n:350", "c:INR"} <= {f.key for f in needed}
    assert missing_figures(needed, figures("Fee: Rs. 350 per month")) == []
    assert [f.key for f in missing_figures(needed, figures("Fee: Rs. 380 per month"))] == ["n:350"]
    assert [f.key for f in missing_figures(needed, figures("Fee: $350 per month"))] == ["c:INR"]
    assert [f.key for f in missing_figures(needed, figures("Fee: 350 per month"))] == ["c:INR"]


def test_a_typed_amount_as_a_decimal_or_an_integer_is_checked_too() -> None:
    needed = claim_figures("c", "l", {"fee": {"amount": Decimal("12.50"), "currency": "EUR"}})
    assert missing_figures(needed, figures("12,50 euros")) == []
    assert [f.key for f in missing_figures(needed, figures("15 euros"))] == ["n:12.5"]
    needed = claim_figures("c", "l", {"limit": {"amount": 5000, "unit": "days"}})
    assert [f.key for f in missing_figures(needed, figures("limit 50 days"))] == ["n:5000"]


def test_strip_unverified_nulls_a_typed_currency_the_quote_does_not_state() -> None:
    data = {"fee": {"amount": "350", "currency": "INR", "period": "per month"}}
    kept = strip_unverified(data, figures("Fee: Rs. 350 per month"))
    assert kept == data
    stripped = strip_unverified(data, figures("Fee: $350 per month"))
    assert stripped["fee"]["currency"] is None and stripped["fee"]["amount"] == "350"
    stripped = strip_unverified(data, figures("Fee: Rs. 380 per month"))
    assert stripped["fee"]["amount"] is None and stripped["fee"]["currency"] == "INR"


# --- other scripts and number words -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("takes 3 days", "prend trois jours"),
        ("takes 2 days", "demora dois dias"),
        ("takes 5 days", "tarda cinco días"),
        ("takes 10 days", "dura diez días"),
        ("1 step", "um passo"),
        ("fee of ₹5,000", "शुल्क ₹५,००० है"),  # Devanagari digits
        ("fee of ₹5,000", "the fee is Rs 5,000/-"),
    ],
)
def test_number_words_of_other_languages_and_scripts_back_a_digit(claim: str, quote: str) -> None:
    assert backed(claim, quote), (claim, quote)


def test_an_english_claim_is_not_asked_for_a_foreign_number_word() -> None:
    assert figures("Dos and Don'ts of onboarding", claim_side=True) == []
    assert keys("a once a day limit") == []


# --- properties ------------------------------------------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=10**9))
def test_an_integer_matches_itself_with_space_dot_and_comma_groups(n: int) -> None:
    groups = f"{n:,}"
    for sep in (" ", " ", " ", " ", ","):
        assert backed(str(n), groups.replace(",", sep)), (n, sep)
    if n >= 1_000_000:  # one dot group is a decimal, so only two or more are thousands
        assert backed(str(n), groups.replace(",", "."))


@given(st.integers(min_value=0, max_value=10**6), st.integers(min_value=0, max_value=99))
def test_a_decimal_comma_equals_the_decimal_point(whole: int, cents: int) -> None:
    point, comma = f"{whole}.{cents:02d}", f"{whole},{cents:02d}"
    assert missing_figures(figures(point), figures(comma)) == []
    assert missing_figures(figures(comma), figures(point)) == []


@given(st.text(max_size=120))
def test_figures_with_currencies_never_raise_and_are_deterministic(text: str) -> None:
    assert figures(text) == figures(text)
    assert figures(text, claim_side=True) == figures(text, claim_side=True)
