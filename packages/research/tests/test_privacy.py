"""Query privacy: forbidden names never survive into a search query."""

from __future__ import annotations

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_research.privacy import QueryPrivacy, compact, fold, terms_from_url


def restricted(*names: str, urls: tuple[str, ...] = (), org: str | None = None) -> QueryPrivacy:
    return QueryPrivacy.build(allow_company_name=False, names=names, urls=urls, org=org)


def test_unrestricted_policy_changes_nothing() -> None:
    privacy = QueryPrivacy.build(allow_company_name=True, names=["Acme"], urls=["https://acme.com"])
    assert not privacy.restricted
    assert privacy.violations("acme shop features") == []
    assert privacy.scrub("acme shop features") == "acme shop features"
    assert privacy.clean_query("acme shop features") == "acme shop features"


@pytest.mark.parametrize(
    "query",
    [
        "features of Acme Corp apps",
        "features of acme corp apps",
        "features of ACME-CORP apps",
        "features of AcmeCorp apps",
        "features of acme_corp apps",
        "features of Acme   Corp apps",
        "features of Acme, Corp apps",
    ],
)
def test_name_variants_are_found(query: str) -> None:
    assert restricted("Acme Corp").violations(query)


def test_word_boundaries_avoid_false_positives_for_short_names() -> None:
    privacy = restricted("Zed")
    assert privacy.violations("zed shop features")
    assert privacy.violations("shop features zed")
    assert not privacy.violations("zedlike shops")  # short names match whole words only
    assert not privacy.violations("organized shops")


def test_long_names_also_match_inside_a_word() -> None:
    privacy = restricted("Handcraft")
    assert privacy.violations("the handcraftedstore app")
    assert privacy.violations("handcraft")


def test_accents_and_case_are_folded() -> None:
    privacy = restricted("Café Noir")
    assert privacy.violations("cafe noir menu apps")
    assert privacy.violations("CAFÉ NOIR menu apps")
    assert fold("Straße") == "strasse"
    assert compact("A-b_C d") == "abcd"


def test_website_terms() -> None:
    assert terms_from_url("https://www.Acme-Shop.co.uk/home") == [
        "acme-shop.co.uk",
        "acme-shop",
    ]
    assert terms_from_url("acme.com") == ["acme.com", "acme"]
    assert terms_from_url(None) == []
    privacy = restricted(urls=("https://www.acme-shop.com/x",))
    assert privacy.violations("brand of acme-shop.com site")
    assert privacy.violations("brand of acme shop")


def test_org_id_is_forbidden_but_not_its_generic_words() -> None:
    privacy = restricted(org="example-services-firm")
    assert privacy.violations("work for example-services-firm apps")
    assert privacy.violations("work for example services firm apps")
    assert not privacy.violations("services booking apps for a firm")
    assert not restricted(org="platform").terms


def test_scrub_removes_the_name_and_tidies_spaces() -> None:
    privacy = restricted("Acme Corp")
    assert privacy.scrub("features of Acme Corp apps") == "features of apps"
    assert privacy.scrub("Acme Corp and AcmeCorp") == "and"
    assert privacy.scrub("An app by Acme Corp for shops", "the company") == (
        "An app by the company for shops"
    )


def test_scrub_keeps_other_text_intact_around_accents() -> None:
    privacy = restricted("Zed")
    assert privacy.scrub("Café Zed menu") == "Café menu"


def test_clean_query_drops_queries_with_nothing_left() -> None:
    privacy = restricted("Acme")
    assert privacy.clean_query("Acme") is None
    assert privacy.clean_query("Acme app") is None  # one word left
    assert privacy.clean_query("features of Acme shops") == "features of shops"


def test_clean_query_rejects_a_name_hidden_inside_another_word() -> None:
    privacy = restricted("Handcraft")
    assert privacy.clean_query("handcraftedstore features for apps") is None


def test_empty_and_blank_names_are_ignored() -> None:
    privacy = QueryPrivacy.build(allow_company_name=False, names=[None, "", "  ", "Acme"])
    assert privacy.terms == ("Acme",)


@settings(max_examples=120, deadline=None, derandomize=True)
@given(
    name=st.text(alphabet=string.ascii_letters, min_size=6, max_size=14),
    separator=st.sampled_from(["", " ", "-", "_", "  "]),
    prefix=st.lists(st.sampled_from(["common", "features", "of", "mobile", "apps"]), max_size=3),
    suffix=st.lists(st.sampled_from(["shops", "ordering", "guide"]), min_size=1, max_size=3),
)
def test_no_surviving_query_ever_contains_the_name(
    name: str, separator: str, prefix: list[str], suffix: list[str]
) -> None:
    half = len(name) // 2
    spelled = name[:half] + separator + name[half:]
    privacy = restricted(name)
    query = " ".join([*prefix, spelled, *suffix])
    cleaned = privacy.clean_query(query)
    if cleaned is not None:
        assert compact(name) not in compact(cleaned)
        assert not privacy.violations(cleaned)
        assert len(cleaned.split()) >= 2


@settings(max_examples=80, deadline=None, derandomize=True)
@given(
    name=st.text(alphabet=string.ascii_letters, min_size=2, max_size=5),
    prefix=st.lists(st.sampled_from(["common", "features", "of", "mobile", "apps"]), max_size=3),
    suffix=st.lists(st.sampled_from(["shops", "ordering", "guide"]), min_size=1, max_size=3),
)
def test_short_names_are_removed_as_whole_words(
    name: str, prefix: list[str], suffix: list[str]
) -> None:
    privacy = restricted(name)
    query = " ".join([*prefix, name, *suffix])
    cleaned = privacy.clean_query(query)
    if cleaned is not None:
        assert name.casefold() not in cleaned.casefold().split()
