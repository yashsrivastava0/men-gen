"""The privacy guard: what a Brief makes private, and whether a query names any of it (``core/privacy.py``).

The owner's rule (2026-11-13): the client's own name and private facts never leave in a query to a search provider.
Every private value below is invented for the test; none is a real person, number or address.
"""

from __future__ import annotations

from typing import Any

import pytest

from mde_research_agent.core.privacy import build_private_terms, find_private
from mde_research_agent.domain.input import Fact, PrivateTerm, PrivateTerms

GENERIC = ["rules", "features", "pricing", "studio", "app"]


def brief_with(*texts: str, **extra: Any) -> dict[str, Any]:
    return {
        "domain_facts": [{"id": f"FCT-{i:03d}", "statement": t} for i, t in enumerate(texts)],
        **extra,
    }


def terms_of(brief: dict[str, Any], **kw: Any) -> PrivateTerms:
    return build_private_terms(brief, **{"generic": GENERIC, **kw})


def kinds(terms: PrivateTerms) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in terms.terms:
        out.setdefault(t.kind, []).append(t.text)
    return out


# --- what a Brief makes private ------------------------------------------------------------------------------------------


def test_the_brand_and_own_business_names_are_private() -> None:
    terms = terms_of({}, own_brand=["Zzyzx Kraal", "Quill & Co"])
    assert kinds(terms) == {"brand": ["Zzyzx Kraal", "Quill & Co"]}


def test_a_term_made_only_of_ordinary_words_is_dropped_not_a_block_on_everything() -> None:
    terms = terms_of({}, own_brand=["Studio", "Pricing Rules", "Ab", "Zzyzx Studio"])
    assert kinds(terms) == {
        "brand": ["Zzyzx Studio"]
    }  # "Ab" is too short; the generic ones would block every query


def test_phones_emails_and_addresses_are_found_in_what_the_person_said() -> None:
    brief = brief_with(
        "Call the front desk on +91 98765 43210 or (020) 2551 4433 after nine.",
        "Mail owner.name@example-clinic.test for the invoice copies.",
        "The workshop is at 221B Baker Street, near the park.",
        "Collection from Rua das Flores 15 on Saturdays.",
        "The warehouse is Shop 12, Plot No. 15.",
    )
    found = kinds(terms_of(brief))
    assert set(found) == {"phone", "email", "address"}
    assert any("98765 43210" in p for p in found["phone"]) and len(found["phone"]) == 2
    assert found["email"] == ["owner.name@example-clinic.test"]
    addresses = " | ".join(found["address"]).casefold()
    for part in (
        "221b baker street",
        "baker street",
        "rua das flores 15",
        "shop 12",
        "plot no. 15",
    ):
        assert part in addresses, part


def test_numbers_dates_prices_and_ids_are_not_phones() -> None:
    brief = brief_with(
        "Prices start at 1,50,000 rupees and 2 500 000 in the large plan.",
        "Approved on 2026-11-10 and again on 10/11/2026.",
        "About 400 trucks, 6 cities, 152 facts and 30 features.",
        "Pin code 411001.",
    )
    assert "phone" not in kinds(terms_of(brief))


def test_bookkeeping_keys_are_not_scanned() -> None:
    brief = {
        "_hash": "x" * 5 + " +91 98765 43210",
        "provenance": [{"date": "+91 98765 43210"}],
        "id": "12 Main Street",
    }
    assert not terms_of(brief).terms
    assert kinds(terms_of({"provenance": [{"quote": "ring +91 98765 43210"}]})) == {
        "phone": ["+91 98765 43210"]
    }


def test_a_place_the_brief_states_is_never_the_brand() -> None:
    """A stated city is where the app is used: passing places as ordinary words keeps it out of the private terms."""
    assert not terms_of({}, own_brand=["Pune"], generic=[*GENERIC, "pune"]).terms
    assert kinds(build_private_terms({}, own_brand=["Pune"], generic=["pune"])) == {}


def test_people_and_sensitive_values_come_from_facts_typed_that_way() -> None:
    facts = [
        Fact(
            id="FCT-1",
            type="person",
            statement="Dr Anna Okoye signs the forms.",
            entities=("Anna Okoye",),
        ),
        Fact(
            id="FCT-2",
            type="sensitive",
            statement="The vault code is 4455-ZZ",
            entities=("vault code 4455",),
        ),
        Fact(
            id="FCT-3", type="context", statement="We run a bakery.", entities=("Corner Bakery",)
        ),  # not typed so
    ]
    found = kinds(terms_of({}, facts=facts))
    assert found["person"] == ["Anna Okoye"]
    assert (
        "vault code 4455" in found["sensitive"]
        and "The vault code is 4455-ZZ" in found["sensitive"]
    )
    assert "Corner Bakery" not in [t for texts in found.values() for t in texts]


def test_the_number_of_terms_is_bounded() -> None:
    brief = brief_with(*[f"Call +91 9{i:04d} 5{i:04d}" for i in range(1000)])
    assert len(terms_of(brief).terms) <= 300


# --- whether a query names any of it -------------------------------------------------------------------------------------

BRAND = PrivateTerm(kind="brand", text="Zzyzx Kraal")
PHONE = PrivateTerm(kind="phone", text="+91 98765 43210")
EMAIL = PrivateTerm(kind="email", text="owner.name@example-clinic.test")
STREET = PrivateTerm(kind="address", text="221B Baker Street")
ALL = PrivateTerms(terms=(BRAND, PHONE, EMAIL, STREET))


@pytest.mark.parametrize(
    ("query", "hit"),
    [
        ("Zzyzx Kraal class booking features", "Zzyzx Kraal"),
        ("class booking features zzyzx kraal", "Zzyzx Kraal"),
        ('"Zzyzx Kraal" pricing', "Zzyzx Kraal"),
        ("zzyzx-kraal reviews bengaluru", "Zzyzx Kraal"),
        ("Zzyzx Kraal's opening hours", "Zzyzx Kraal"),
        ("who answers 98765 43210 clinic", "+91 98765 43210"),
        ("9876543210 clinic opening hours", "+91 98765 43210"),
        ("+91-98765-43210 reviews", "+91 98765 43210"),
        ("write to owner.name@example-clinic.test about booking", "owner.name@example-clinic.test"),
        ("OWNER.NAME@EXAMPLE-CLINIC.TEST", "owner.name@example-clinic.test"),
        ("repair shop 221b baker street opening hours", "221B Baker Street"),
        ("bike repair at 221B  Baker   Street Porto", "221B Baker Street"),
    ],
)
def test_an_injected_leak_is_found(query: str, hit: str) -> None:
    assert find_private(query, ALL) == [hit]


@pytest.mark.parametrize(
    "query",
    [
        "yoga studio class booking features Bengaluru",
        "Which payment gateways support UPI refunds in India",
        "zzyzx pricing",  # half a name is not the name
        "kraal fencing rules",
        "Zzyzx Kraalton listings",  # a different word
        "98765 clinic opening hours",  # a few digits are not the number
        "2026 2027 2028 budget rules",  # years are not phones
        "baker street",  # the street without the house number is not the address
        "mail us at the studio",
        "",
        "   ",
    ],
)
def test_a_clean_query_goes(query: str) -> None:
    assert find_private(query, ALL) == []


def test_no_terms_means_nothing_to_find() -> None:
    assert find_private("Zzyzx Kraal pricing", PrivateTerms()) == []
    assert find_private("Zzyzx Kraal pricing", ()) == []


def test_terms_may_be_a_plain_list() -> None:
    assert find_private("zzyzx kraal", [BRAND]) == ["Zzyzx Kraal"]


def test_an_address_found_in_a_brief_is_caught_with_and_without_its_number() -> None:
    brief = brief_with("The workshop is at 221B Baker Street, near the park.")
    terms = terms_of(brief)
    assert find_private("repair 221B Baker Street", terms)
    assert find_private(
        "bike repair baker street porto", terms
    )  # the street name alone is a term too
