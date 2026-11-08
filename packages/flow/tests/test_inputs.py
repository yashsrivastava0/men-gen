"""Request inputs derived from stored state: named apps and the brand site (pure functions)."""

from __future__ import annotations

import pytest

from mde_contracts.slots import SlotRecord
from mde_flow.inputs import (
    MAX_REFERENCES,
    brand_url,
    clean_url,
    references,
)


def slot(
    slot_id: str, value: object, *, state: str = "confirmed", source: str | None = "text"
) -> SlotRecord:
    return SlotRecord.model_validate(
        {"slot_id": slot_id, "value": value, "state": state, "source": source}
    )


# --------------------------------------------------------------------------- references


def test_references_are_trimmed_deduplicated_and_capped() -> None:
    assert references(["  Zomato ", "zomato", "Swiggy", "", "   ", "Swiggy "]) == [
        "Zomato",
        "Swiggy",
    ]
    assert references([]) == []
    many = [f"App {i}" for i in range(30)]
    assert references(many) == many[:MAX_REFERENCES]
    assert references(["a  b\n c"]) == ["a b c"]  # whitespace collapsed
    assert len(references(["x" * 500])[0]) == 120


# --------------------------------------------------------------------------- clean_url


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("https://acme.example", "https://acme.example"),
        ("https://Acme.Example/", "https://acme.example"),
        ("http://acme.example/brand/", "http://acme.example/brand"),
        ("www.acme.example", "https://www.acme.example"),
        ("https://acme.example/brand?utm=1#top", "https://acme.example/brand"),
        ("https://acme.example.)", "https://acme.example"),  # trailing punctuation
        ("https://shop.acme.co.in:443/x", "https://shop.acme.co.in/x"),
        ("https://acme.example.", "https://acme.example"),
    ],
)
def test_a_public_site_is_cleaned(raw: str, clean: str) -> None:
    assert clean_url(raw) == clean


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://acme.example",
        "javascript:alert(1)",
        "https://user:secret@acme.example",
        "https://user@acme.example",
        "https://localhost/admin",
        "http://127.0.0.1:8000",
        "http://10.0.0.5",
        "http://[::1]/",
        "https://intranet",  # a single label is not a public site
        "https://wiki.corp.internal",
        "https://printer.local",
        "https://acme.example:8443/x",
        "https://" + "a" * 70 + ".example",
        "https://acme..example",
        "https://acme.example/" + "p" * 2100,
        "not a url",
    ],
)
def test_anything_that_is_not_a_plain_public_site_is_refused(raw: str) -> None:
    assert clean_url(raw) is None


# --------------------------------------------------------------------------- brand_url


def test_a_site_the_user_typed_in_the_brand_assets_is_used() -> None:
    slots = {
        "brand.assets": slot("brand.assets", "https://acme.example/brand", state="volunteered")
    }
    assert brand_url(slots) == "https://acme.example/brand"


def test_a_site_pasted_as_the_brand_name_is_used() -> None:
    slots = {"brand.name": slot("brand.name", "Acme www.acme.example", state="volunteered")}
    assert brand_url(slots) == "https://www.acme.example"


def test_assets_are_looked_at_before_the_name_and_the_first_good_link_wins() -> None:
    slots = {
        "brand.assets": slot(
            "brand.assets", ["logo.png", "see https://acme.example/a and https://other.example"]
        ),
        "brand.name": slot("brand.name", "https://name.example", state="volunteered"),
    }
    assert brand_url(slots) == "https://acme.example/a"
    only_name = {"brand.name": slot("brand.name", "https://name.example", state="volunteered")}
    assert brand_url(only_name) == "https://name.example"


def test_a_bad_link_is_skipped_for_the_next_one() -> None:
    slots = {
        "brand.assets": slot(
            "brand.assets",
            "https://user:pw@evil.example then https://good.example",
            state="volunteered",
        )
    }
    assert brand_url(slots) == "https://good.example"


@pytest.mark.parametrize("state", ["assumed", "disputed", "empty"])
def test_only_a_site_the_user_gave_or_confirmed_counts_by_state(state: str) -> None:
    slots = {"brand.assets": slot("brand.assets", "https://acme.example", state=state)}
    assert brand_url(slots) is None


@pytest.mark.parametrize("source", ["default", "research", "upload", None])
def test_a_site_from_a_default_research_or_an_upload_is_never_used(source: str | None) -> None:
    slots = {"brand.assets": slot("brand.assets", "https://acme.example", source=source)}
    assert brand_url(slots) is None


@pytest.mark.parametrize("source", ["tap", "text"])
@pytest.mark.parametrize("state", ["volunteered", "confirmed"])
def test_a_site_the_user_typed_or_tapped_counts(source: str, state: str) -> None:
    slots = {
        "brand.assets": slot("brand.assets", "https://acme.example", state=state, source=source)
    }
    assert brand_url(slots) == "https://acme.example"


def test_a_standing_profile_site_counts_only_when_confirmed() -> None:
    for source in ("org_profile", "memory"):
        confirmed = {"brand.assets": slot("brand.assets", "https://acme.example", source=source)}
        assert brand_url(confirmed) == "https://acme.example"
        offered = {
            "brand.assets": slot(
                "brand.assets", "https://acme.example", state="volunteered", source=source
            )
        }
        assert brand_url(offered) is None


def test_a_name_without_a_link_gives_nothing() -> None:
    assert brand_url({"brand.name": slot("brand.name", "Acme Foods")}) is None
    assert brand_url({}) is None
    assert brand_url({"brand.assets": slot("brand.assets", True)}) is None
    assert brand_url({"brand.assets": slot("brand.assets", None)}) is None
    assert brand_url({"other": slot("scope.features", ["https://acme.example"])}) is None
