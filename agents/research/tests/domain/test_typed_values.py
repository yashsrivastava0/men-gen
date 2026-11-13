"""Typed values and dossier field keys are checked by code against the quote (D-159, D-162).

A typed value is the machine-readable form of a fee, a limit or a time. The model writes it next to the quote; code decides
whether the quote backs it. A wrong amount or a currency the quote does not show rejects the fact, one item at a time
(``payload_from_wire`` raises, the candidate becomes ``Rejected(invalid)``); a unit, period or condition the quote does not use
becomes null and the fact stays.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from support.testing import atom

from mde_research_agent.calls.extract import extract_out_model
from mde_research_agent.catalog.content import dossier_config
from mde_research_agent.domain.atoms import TypedValue, payload_from_wire, wire_atom_model
from mde_research_agent.domain.typed import (
    field_problem,
    ground_provider_fact,
    ground_typed,
    normalize_amount,
    normalize_field,
)
from mde_research_agent.engine.verify import candidates_from_output

FEE = "Card payments cost 2.9% + $0.30 per successful transaction, charged monthly."


def typed(**kw: Any) -> dict[str, Any]:
    base = {"amount": None, "currency": None, "unit": None, "period": None, "condition": None}
    return {**base, **kw}


# --- the amount ----------------------------------------------------------------------------------------------------


def test_an_amount_in_the_quote_is_kept() -> None:
    got, problems = ground_typed(typed(amount="2.9", unit="percent"), FEE)
    assert problems == [] and got == typed(amount="2.9", unit="percent")


def test_a_wrong_amount_is_rejected() -> None:
    _, problems = ground_typed(typed(amount="3.5", unit="percent"), FEE)
    assert problems and "3.5" in problems[0] and "not in the quote" in problems[0]


@pytest.mark.parametrize("bad", ["2.9%", "about 3", "1e3", "-4", "two", "2,9,0", "$0.30"])
def test_an_amount_that_is_not_a_plain_decimal_string_is_rejected(bad: str) -> None:
    _, problems = ground_typed(typed(amount=bad), FEE)
    assert problems and "decimal" in problems[0]


def test_thousands_separators_are_normalised_and_still_checked() -> None:
    quote = "The daily payout limit is 1,500 per merchant."
    got, problems = ground_typed(typed(amount="1,500", unit="merchant"), quote)
    assert problems == [] and got is not None and got["amount"] == "1500"
    assert normalize_amount("1,500.50") == "1500.50"
    _, wrong = ground_typed(typed(amount="1,600"), quote)
    assert wrong


def test_money_is_never_a_float() -> None:
    with pytest.raises(ValidationError):
        TypedValue(amount=2.9)  # type: ignore[arg-type]
    assert isinstance(Decimal(normalize_amount("0.30") or "x"), Decimal)


# --- the currency ---------------------------------------------------------------------------------------------------


def test_a_currency_the_quote_shows_by_symbol_or_code_is_kept() -> None:
    got, problems = ground_typed(typed(amount="0.30", currency="usd"), FEE)
    assert problems == [] and got is not None and got["currency"] == "USD"
    got, problems = ground_typed(
        typed(amount="12.50", currency="EUR"), "The fee is 12.50 EUR per payout."
    )
    assert problems == [] and got is not None and got["currency"] == "EUR"


def test_a_currency_the_quote_does_not_show_is_rejected() -> None:
    _, problems = ground_typed(typed(amount="0.30", currency="EUR"), FEE)
    assert problems and "EUR" in problems[0] and "not shown" in problems[0]


def test_a_currency_that_is_not_a_code_is_rejected() -> None:
    _, problems = ground_typed(typed(amount="0.30", currency="dollars"), FEE)
    assert problems and "three-letter" in problems[0]


def test_a_single_letter_symbol_only_counts_next_to_a_number() -> None:
    got, problems = ground_typed(
        typed(amount="350", currency="ZAR"), "The plan costs R 350 a month."
    )
    assert problems == [] and got is not None
    _, problems = ground_typed(typed(amount="350", currency="ZAR"), "Plan R is 350 a month.")
    assert problems, "a lone R that is not next to the number is not a currency"


# --- unit, period, condition: kept when the quote uses the words, else null --------------------------------------------


def test_a_unit_period_or_condition_the_quote_does_not_use_becomes_null_and_the_fact_stays() -> (
    None
):
    got, problems = ground_typed(
        typed(amount="2.9", unit="percent", period="year", condition="above 5000"), FEE
    )
    assert problems == []
    assert got is not None and got["amount"] == "2.9" and got["unit"] == "percent"
    assert got["period"] is None, "the quote says monthly, not year"
    assert got["condition"] is None, "5000 is not in the quote"


def test_word_forms_of_the_quote_count() -> None:
    got, _ = ground_typed(typed(amount="2.9", period="month", unit="transaction"), FEE)
    assert got is not None and got["period"] == "month" and got["unit"] == "transaction"


def test_a_typed_value_with_nothing_left_is_null() -> None:
    assert ground_typed(None, FEE) == (None, [])
    got, problems = ground_typed(typed(unit="barrel"), FEE)
    assert got is None and problems == []


# --- the dossier field key ------------------------------------------------------------------------------------------------


def test_every_extractable_field_key_is_accepted_and_others_are_not() -> None:
    for key in dossier_config().extractable():
        assert field_problem(key) is None
    assert field_problem("favourite colour")
    assert field_problem("official_docs"), "official_docs is derived by code, never extracted"


def test_a_field_key_is_normalised() -> None:
    assert normalize_field(" Webhook Events ") == "webhook_events"
    data = {
        "provider": "Payco",
        "need": "payments",
        "field": "Webhooks events",
        "value": "x",
        "typed": None,
        "countries": [],
    }
    assert ground_provider_fact(data, "Webhooks events are sent.")["field"] == "webhooks_events"


# --- through the wire: one item at a time -------------------------------------------------------------------------------


def wire(**payload: Any) -> Any:
    fields: dict[str, Any] = {
        "provider": "Payco",
        "need": "payments",
        "field": "fees",
        "value": "2.9% + $0.30",
        "typed": None,
        "countries": [],
    }
    _, a = atom("provider_fact", FEE, key="payco fee", **{**fields, **payload})
    return wire_atom_model("provider_fact").model_validate(a)


def test_payload_from_wire_keeps_a_grounded_typed_value() -> None:
    data = payload_from_wire("provider_fact", wire(typed=typed(amount="2.9", unit="percent")))
    assert data["typed"]["amount"] == "2.9" and data["field"] == "fees"


@pytest.mark.parametrize(
    "bad",
    [typed(amount="7.5"), typed(amount="0.30", currency="EUR"), typed(amount="two")],
)
def test_payload_from_wire_rejects_a_wrong_typed_value(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        payload_from_wire("provider_fact", wire(typed=bad))


def test_a_wrong_typed_value_rejects_only_that_item_not_the_whole_page() -> None:
    """The model's other items on the page survive: the failure is per candidate (``Rejected(invalid)``)."""
    from datetime import date

    from support.testing import FIXTURES  # noqa: F401  (the helper module is importable here)

    from mde_research_agent.engine.state import PageRecord

    good = atom(
        "provider_fact",
        FEE,
        key="a",
        provider="Payco",
        need="payments",
        field="fees",
        value="2.9%",
        typed=typed(amount="2.9"),
        countries=[],
    )
    bad = atom(
        "provider_fact",
        FEE,
        key="b",
        provider="Payco",
        need="payments",
        field="fees",
        value="9%",
        typed=typed(amount="9"),
        countries=[],
    )
    out = extract_out_model("services").model_validate(
        {"provider_fact": [good[1], bad[1]], "service_option": [], "compliance_note": []}
    )
    page = PageRecord(
        id="pg_1",
        url="https://docs.payco.example/fees",
        final_url="https://docs.payco.example/fees",
        domain="docs.payco.example",
        source_class="official",
        title="Fees",
        text=FEE,
        content_hash="h",
        text_source="fetch",
        published=None,
        retrieved_on=date(2026, 11, 11),
    )
    got = candidates_from_output(out, page)
    assert [type(c).__name__ for c in got] == ["Candidate", "Rejected"]
    assert got[1].reason == "invalid"  # type: ignore[union-attr]


# --- registration in the number check ---------------------------------------------------------------------------------


def test_typed_value_keys_are_registered_in_the_number_check() -> None:
    from mde_research_agent.core.numbers import NUMERIC_FIELDS

    assert {"amount", "period", "unit", "condition", "penalty"} <= set(NUMERIC_FIELDS)


def test_check_candidate_rejects_a_typed_amount_the_quote_lacks() -> None:
    from datetime import date

    from mde_research_agent.engine.state import PageRecord
    from mde_research_agent.engine.verify import Candidate, Rejected, check_candidate

    page = PageRecord(
        id="pg_1",
        url="https://docs.payco.example/fees",
        final_url="https://docs.payco.example/fees",
        domain="docs.payco.example",
        source_class="official",
        title="Fees",
        text=FEE,
        content_hash="h",
        text_source="fetch",
        published=None,
        retrieved_on=date(2026, 11, 11),
    )
    cand = Candidate(
        kind="provider_fact",
        norm_key="payco fee",
        label="Payco fee",
        claim="Payco charges cards.",
        quote=FEE,
        impact="high",
        published_on=None,
        data={
            "provider": "Payco",
            "need": "payments",
            "field": "fees",
            "value": "card fee",
            "typed": typed(amount="7.5", unit="percent"),
            "countries": [],
        },
    )
    assert isinstance(check_candidate(cand, page, {}), Rejected)
