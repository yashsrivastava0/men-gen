"""RV-18 in the pipeline: numbers must be in the quote (widened to the sentence, or rejected), dates come from code,
claims are never clipped mid-sentence."""

from __future__ import annotations

from datetime import date
from typing import Any

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


def rule(quote: str, claim: str, **kw: Any) -> tuple[str, dict]:
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


# --- the figure drop: the three provider items of the Small run, rebuilt ---------------------------------------------

DROP = {"service_option": ["fees", "limits", "constraints"], "competitor": ["pricing_model"]}
SERVICES_TEXT = (
    "MessageBot offers transactional, promotional and OTP SMS for India. A REST API with webhook support is included. "
    "Transactional SMS costs Rs. 0.16 to Rs. 0.25 per SMS depending on volume, plus 18% GST. "
    "WA.Expert exposes webhooks that push events to your server in real time. "
    "Full developer API access is included in the Complete plan at Rs. 599 a month."
)


def services(quote: str, claim: str, label: str, **payload: Any) -> Candidate | Rejected:
    pg = page(SERVICES_TEXT)
    out = candidates_from_output(
        extract_out_model("services").model_validate(
            {
                "service_option": [
                    atom(
                        "service_option",
                        quote,
                        key="provider x",
                        label=label,
                        claim=claim,
                        **payload,
                    )[1]
                ],
                "compliance_note": [],
            }
        ),
        pg,
    )[0]
    assert isinstance(out, Candidate), out
    return check_candidate(out, pg, DROP)


def test_fees_that_only_the_data_states_are_dropped_and_the_item_is_kept() -> None:
    """MessageBot: the quote is the endpoint sentence; the fees are in data.fees."""
    out = services(
        "A REST API with webhook support is included.",
        "MessageBot offers transactional SMS for India with a REST API and webhook support.",
        "MessageBot SMS",
        fees="Transactional SMS costs Rs. 0.16 to Rs. 0.25 per SMS plus 18% GST.",
    )
    assert isinstance(out, Candidate) and "figures_dropped" in out.flags
    assert out.data["fees"] is None and out.claim.startswith("MessageBot offers")


def test_a_later_claim_sentence_with_an_unverified_figure_is_cut() -> None:
    """WA.Expert: sentence one is backed by the quote, sentence two states Rs. 599."""
    out = services(
        "WA.Expert exposes webhooks that push events to your server in real time.",
        "WA.Expert sends webhook events in real time. Developer API access is in the Complete plan at Rs. 599 a month.",
        "WA.Expert WhatsApp API",
        fees="Included in the Complete plan at Rs. 599 a month.",
    )
    assert isinstance(out, Candidate) and "figures_dropped" in out.flags
    assert out.claim == "WA.Expert sends webhook events in real time." and out.data["fees"] is None


def test_a_figure_in_the_first_sentence_or_the_label_still_rejects() -> None:
    first = services(
        "A REST API with webhook support is included.",
        "Approval takes 2-3 weeks and a REST API with webhook support is included.",
        "MessageBot SMS",
    )
    assert isinstance(first, Rejected) and first.reason == "number_not_in_quote"
    label = services(
        "A REST API with webhook support is included.",
        "MessageBot has a REST API.",
        "MessageBot 99 SMS",
    )
    assert isinstance(label, Rejected)


def test_kinds_that_are_not_listed_never_drop_a_figure() -> None:
    """A rule's figure IS the finding: with the claim clean and only data.value unverified the item is still rejected."""
    pg = page()
    c = first(
        candidates(
            [
                rule(
                    "Settlement takes 3 business days after capture",
                    "Settlement is quick.",
                    value="7",
                )
            ],
            pg,
        )
    )
    assert isinstance(check_candidate(c, pg, DROP), Rejected)
    assert isinstance(check_candidate(c, pg), Rejected)  # no map at all: the old behaviour


def test_what_survives_a_drop_holds_no_unverified_figure() -> None:
    from mde_research_agent.core.numbers import claim_figures, figures, missing_figures

    out = services(
        "A REST API with webhook support is included.",
        "MessageBot has a REST API. It costs Rs. 0.16 per SMS.",
        "MessageBot SMS",
        fees="Rs. 0.16 per SMS",
        limits="18% GST applies",
    )
    assert isinstance(out, Candidate)
    assert not missing_figures(claim_figures(out.claim, out.label, out.data), figures(out.quote))
