"""RV-18 in the pipeline: numbers must be in the quote (widened to the sentence, or rejected), dates come from code,
claims are never clipped mid-sentence."""

from __future__ import annotations

from datetime import date

from support.testing import atom

from mde_research_agent.calls.extract import extract_out_model
from mde_research_agent.engine.state import PageRecord
from mde_research_agent.engine.verify import (
    Candidate,
    Rejected,
    candidates_from_output,
    check_candidate,
    cut_claim,
    region_of,
)

PAGE_TEXT = (
    "Payments overview. The gateway charges a fee of 2.5% on every card payment. "
    "Settlement takes 3 business days after capture. Refunds are free of charge. "
    "Pricing table\nFee | 2.5%\nPayout | Rs. 3 per transfer\nUpdated March 2026."
)


def page(
    text: str = PAGE_TEXT, published: date | None = None, title: str = "Pricing"
) -> PageRecord:
    return PageRecord(
        id="pg_1",
        url="https://pay.example/pricing",
        final_url="https://pay.example/pricing",
        domain="pay.example",
        source_class="official",
        title=title,
        text=text,
        content_hash="h",
        text_source="fetch",
        published=published,
        retrieved_on=date(2026, 11, 11),
    )


def candidates(items: list[tuple[str, dict]], pg: PageRecord) -> list[Candidate | Rejected]:
    data: dict[str, list[dict]] = {
        k: [] for k in ("entity_pattern", "lifecycle", "rule", "compliance_note")
    }
    for kind, a in items:
        data[kind].append(a)
    return candidates_from_output(extract_out_model("data").model_validate(data), pg)


def rule(quote: str, claim: str, **kw: object) -> tuple[str, dict]:
    return atom(
        "rule", quote, key="card fee", label="Card payment fee", claim=claim, impact="high", **kw
    )


def first(items: list[Candidate | Rejected]) -> Candidate:
    assert isinstance(items[0], Candidate), items[0]
    return items[0]


def test_a_quote_that_holds_the_numbers_passes_unchanged() -> None:
    pg = page()
    c = first(
        candidates(
            [
                rule(
                    "charges a fee of 2.5% on every card payment",
                    "The gateway charges 2.5% per card payment.",
                    value="2.5%",
                )
            ],
            pg,
        )
    )
    out = check_candidate(c, pg)
    assert isinstance(out, Candidate) and out.quote == c.quote and not out.flags


def test_a_number_missing_from_the_quote_but_in_the_sentence_widens_the_quote() -> None:
    pg = page()
    c = first(
        candidates(
            [
                rule(
                    "every card payment",
                    "The gateway charges 2.5% on every card payment.",
                    value="2.5%",
                )
            ],
            pg,
        )
    )
    out = check_candidate(c, pg)
    assert isinstance(out, Candidate)
    assert out.quote == "The gateway charges a fee of 2.5% on every card payment."
    assert "number_widened" in out.flags


def test_a_number_that_is_nowhere_near_the_quote_is_rejected() -> None:
    pg = page()
    c = first(
        candidates(
            [
                rule(
                    "Refunds are free of charge",
                    "Refunds are free but capped at 500 per month.",
                    value="500",
                )
            ],
            pg,
        )
    )
    out = check_candidate(c, pg)
    assert isinstance(out, Rejected) and out.reason == "number_not_in_quote"


def test_a_number_in_the_data_must_be_in_the_quote_too() -> None:
    pg = page()
    c = first(
        candidates(
            [
                rule(
                    "Settlement takes 3 business days after capture",
                    "Settlement is quick.",
                    value="7 days",
                )
            ],
            pg,
        )
    )
    out = check_candidate(c, pg)
    assert isinstance(out, Rejected) and out.reason == "number_not_in_quote"


def test_a_table_cell_quote_verifies_and_matches_formatted_numbers() -> None:
    pg = page()
    c = first(
        candidates(
            [rule("Payout | Rs. 3 per transfer", "A payout costs Rs 3 per transfer.", value="3")],
            pg,
        )
    )
    assert isinstance(check_candidate(c, pg), Candidate)


def test_a_quote_not_on_the_page_is_still_quote_not_found() -> None:
    pg = page()
    c = first(
        candidates([rule("The gateway charges 9% on every card payment", "9%", value="9%")], pg)
    )
    out = check_candidate(c, pg)
    assert isinstance(out, Rejected) and out.reason == "quote_not_found"


def test_the_date_comes_from_the_page_then_from_code_never_from_the_model() -> None:
    dated = page(published=date(2026, 1, 5))
    c = first(
        candidates(
            [rule("Settlement takes 3 business days after capture", "Settlement takes 3 days.")],
            dated,
        )
    )
    assert c.published_on == date(2026, 1, 5)
    undated = page()
    c2 = first(candidates([rule("Updated March 2026.", "Updated in March 2026.")], undated))
    assert c2.published_on == date(2026, 3, 1)
    c3 = first(candidates([rule("Refunds are free of charge", "Refunds are free.")], undated))
    assert (
        c3.published_on is None
    )  # no date on the page, in the quote, the title or the address: none is invented


def test_a_long_claim_is_cut_at_a_sentence_end_or_rejected() -> None:
    sentence = "The provider settles funds to the merchant account quickly. "
    long_two = sentence + "It then sends a report " + "word " * 40
    assert cut_claim(long_two) == "The provider settles funds to the merchant account quickly."
    assert cut_claim("one " * 45) is None
    pg = page()
    out = candidates([rule("Refunds are free of charge", "word " * 55)], pg)
    assert isinstance(out[0], Rejected) and out[0].reason == "claim_too_long"


def test_region_is_a_country_code_global_or_unknown() -> None:
    assert region_of("IN") == "in" and region_of(" gb ") == "gb"
    assert (
        region_of("global") == "global"
        and region_of("India") == "unknown"
        and region_of(None) == "unknown"
    )
