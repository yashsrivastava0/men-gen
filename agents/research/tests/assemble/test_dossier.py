"""Provider dossiers are built by code from checked provider facts (D-159): per field a value, its items, an evidence label and,
when empty, a reason; a conflicting fee keeps both values; completeness and the official share are computed.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from support.content import make_item

from mde_research_agent.assemble.dossier import build_dossiers, provider_groups
from mde_research_agent.catalog import load_catalog

CAT = load_catalog()


def fee(iid: str, amount: str, *, unit: str = "percent", **kw: Any):
    return make_item(
        iid,
        "Payco",
        "fees",
        f"{amount} {unit}",
        typed={"amount": amount, "currency": None, "unit": unit, "period": None, "condition": None},
        **kw,
    )


def one(items):
    dossiers, index = build_dossiers(items, CAT)
    assert len(dossiers) == 1
    return dossiers[0], index


def test_a_conflicting_fee_keeps_both_values_and_defaults_neither() -> None:
    d, _ = one(
        [
            fee("RI-001", "2.9", domain="docs.payco.example"),
            fee(
                "RI-002",
                "3.4",
                domain="blog.example",
                source_class="blog",
                evidence="single_source",
            ),
        ]
    )
    f = d.fields["fees"]
    assert (
        f.value is None and f.typed is None and f.null_reason == "conflict" and f.evidence is None
    )
    assert sorted(v.value for v in f.values) == ["2.9 percent", "3.4 percent"], (
        "both values are kept"
    )
    assert all(v.conflict for v in f.values)
    assert f.item_ids == ["RI-001", "RI-002"]
    assert d.completeness.filled == 1, "only official_docs is filled: a conflicting field is not"


def test_fees_with_different_units_are_two_components_not_a_conflict() -> None:
    d, _ = one([fee("RI-001", "2.9"), fee("RI-002", "0.30", unit="transaction")])
    f = d.fields["fees"]
    assert f.null_reason is None and f.value == "2.9 percent | 0.30 transaction"
    assert not any(v.conflict for v in f.values) and f.typed is None


def test_the_same_amount_from_two_sources_is_one_value() -> None:
    d, _ = one(
        [
            fee("RI-001", "2.9"),
            fee("RI-002", "2.9", domain="help.payco.example", source_class="help"),
        ]
    )
    f = d.fields["fees"]
    assert (
        f.value == "2.9 percent"
        and len(f.values) == 2
        and f.typed is not None
        and f.typed.amount == "2.9"
    )


def test_fees_for_different_countries_do_not_conflict() -> None:
    d, _ = one([fee("RI-001", "2.9", countries=["in"]), fee("RI-002", "3.5", countries=["ae"])])
    assert d.fields["fees"].null_reason is None


def test_a_scalar_field_with_two_values_is_a_conflict_but_a_list_field_is_not() -> None:
    d, _ = one(
        [
            make_item("RI-001", "Payco", "status", "active"),
            make_item("RI-002", "Payco", "status", "deprecated"),
            make_item("RI-003", "Payco", "auth_method", "API key"),
            make_item("RI-004", "Payco", "auth_method", "OAuth token"),
        ]
    )
    assert d.fields["status"].null_reason == "conflict"
    f = d.fields["auth_method"]
    assert f.null_reason is None and f.value == "API key | OAuth token"


def test_a_field_is_only_as_strong_as_its_weakest_value() -> None:
    d, _ = one(
        [
            make_item("RI-001", "Payco", "products", "cards"),
            make_item(
                "RI-002",
                "Payco",
                "products",
                "wallets",
                evidence="single_source",
                source_class="blog",
                domain="blog.example",
            ),
        ]
    )
    f = d.fields["products"]
    assert f.evidence == "single_source"
    assert [v.evidence for v in f.values] == ["official", "single_source"], "per-value labels stay"


def test_unverified_values_are_shown_but_never_count_as_filled() -> None:
    d, _ = one([make_item("RI-001", "Payco", "sandbox", "test keys", evidence="unverified")])
    f = d.fields["sandbox"]
    assert f.value == "test keys" and f.evidence == "unverified" and f.null_reason is None
    assert "sandbox" in d.required and d.completeness.unverified == 1
    assert d.completeness.filled == 1, "only official_docs (derived from the page) is filled"


def test_an_unverified_value_does_not_make_a_conflict_with_a_usable_one() -> None:
    d, _ = one(
        [
            fee("RI-001", "2.9"),
            fee(
                "RI-002", "9.9", evidence="unverified", source_class="forum", domain="forum.example"
            ),
        ]
    )
    f = d.fields["fees"]
    assert f.null_reason is None and f.value == "2.9 percent" and f.evidence == "official"
    assert len(f.values) == 2, "the unverified one is still listed"


def test_empty_fields_say_why() -> None:
    one_page, _ = one([make_item("RI-001", "Payco", "products", "cards")])
    assert one_page.fields["sandbox"].null_reason == "not_researched", (
        "one page read proves nothing about absence"
    )
    two_pages, _ = one(
        [
            make_item("RI-001", "Payco", "products", "cards"),
            make_item("RI-002", "Payco", "countries", "India"),
        ]
    )
    assert two_pages.fields["sandbox"].null_reason == "not_stated_by_sources"
    for d in (one_page, two_pages):
        assert all(f.value is not None or f.null_reason for f in d.fields.values())


def test_the_official_docs_url_comes_from_the_providers_own_official_pages_and_never_from_a_model() -> (
    None
):
    d, _ = one(
        [
            make_item(
                "RI-001",
                "Payco",
                "products",
                "cards",
                domain="blog.example",
                source_class="blog",
                evidence="single_source",
            ),
            make_item("RI-002", "Payco", "sandbox", "test keys", domain="docs.payco.example"),
        ]
    )
    assert d.official_url == "https://docs.payco.example/RI-002"
    f = d.fields["official_docs"]
    assert f.value == d.official_url and f.evidence == "official" and f.item_ids == ["RI-002"]


def test_no_official_docs_url_when_no_official_page_is_the_providers_own() -> None:
    d, _ = one([make_item("RI-001", "Payco", "products", "cards", domain="docs.other.example")])
    assert d.official_url is None and d.fields["official_docs"].null_reason == "not_researched"


def test_completeness_counts_the_required_fields_of_the_integration_need() -> None:
    items = [
        make_item("RI-001", "Payco", "products", "cards"),
        make_item("RI-002", "Payco", "auth_method", "API key"),
        make_item("RI-003", "Payco", "sandbox", "test keys"),
        make_item("RI-004", "Payco", "settlement", "2 business days"),
    ]
    d, _ = one(items)
    required = CAT.dossiers.layout_for("payments").required
    assert d.capability == "payments" and d.required == list(required)
    assert d.completeness.required == len(required)
    # official_docs (derived from docs.payco.example), products, auth_method, sandbox and settlement
    assert d.completeness.filled == 5
    assert d.completeness.share == round(5 / len(required), 3)
    assert d.official_share == 1.0


def test_an_unknown_need_uses_the_default_layout() -> None:
    d, _ = one([make_item("RI-001", "Payco", "products", "cards", need="quantum teleportation")])
    assert d.capability is None and d.required == list(CAT.dossiers.default.required)


def test_a_field_outside_the_layout_is_still_shown() -> None:
    d, _ = one([make_item("RI-001", "Payco", "endpoints", "POST /charges", need="calendar sync")])
    assert "endpoints" in d.fields and d.fields["endpoints"].value == "POST /charges"


def test_names_of_one_provider_become_one_dossier() -> None:
    items = [
        make_item("RI-001", "Payco", "products", "cards"),
        make_item("RI-002", "Payco Payments", "sandbox", "test keys"),
    ]
    d, _ = one(items)
    assert d.provider == "Payco" and d.item_ids == ["RI-001", "RI-002"]
    assert provider_groups(["Payco", "Payco Payments", "Other Pay"])["payco payments"] == "payco"


def test_two_providers_two_dossiers_ordered_by_need_then_name_and_indexed_by_integration() -> None:
    items = [
        make_item("RI-001", "Zeta SMS", "products", "text messages", need="sms messaging"),
        make_item("RI-002", "Alpha Pay", "products", "cards"),
        make_item("RI-003", "Beta Pay", "products", "wallets"),
    ]
    dossiers, index = build_dossiers(items, CAT)
    assert [d.id for d in dossiers] == ["DS-001", "DS-002", "DS-003"]
    assert [(d.provider, d.capability) for d in dossiers] == [
        ("Alpha Pay", "payments"),
        ("Beta Pay", "payments"),
        ("Zeta SMS", "messaging"),
    ]
    assert index == {"messaging": ["DS-003"], "payments": ["DS-001", "DS-002"]}


def test_items_about_another_region_or_entity_are_not_used() -> None:
    items = [
        make_item(
            "RI-001", "Payco", "products", "cards", flags=["other_region"], evidence="unverified"
        )
    ]
    assert build_dossiers(items, CAT) == ([], {})


def test_service_options_of_the_same_provider_are_listed_not_merged() -> None:
    so = make_item(
        "RI-009",
        "Payco",
        "",
        "",
        kind="service_option",
        data={
            "need": "payments",
            "provider": "Payco",
            "region_fit": "India",
            "endpoints_or_events": [],
        },
    )
    d, _ = one([make_item("RI-001", "Payco", "products", "cards"), so])
    assert d.service_option_ids == ["RI-009"] and "RI-009" not in d.item_ids


def test_the_result_does_not_depend_on_the_order_of_the_items() -> None:
    items = [
        fee("RI-001", "2.9"),
        fee("RI-002", "3.4", domain="blog.example", source_class="blog", evidence="single_source"),
        make_item("RI-003", "Payco", "products", "cards"),
        make_item("RI-004", "Beta Pay", "sandbox", "test keys"),
    ]
    want = [d.model_dump() for d in build_dossiers(items, CAT)[0]]
    for seed in range(5):
        shuffled = list(items)
        random.Random(seed).shuffle(shuffled)
        assert [d.model_dump() for d in build_dossiers(shuffled, CAT)[0]] == want


def test_a_front_end_engineer_can_see_every_required_field_in_the_dossier_alone() -> None:
    d, _ = one([make_item("RI-001", "Payco", "products", "cards")])
    assert set(d.required) <= set(d.fields)
    for key in d.required:
        f = d.fields[key]
        assert (f.value is not None) != (f.null_reason is not None), key


@pytest.mark.parametrize("field", ["fees", "limits", "settlement", "onboarding_lead_time"])
def test_typed_fields_are_marked_typed_in_the_layout(field: str) -> None:
    assert CAT.dossiers.fields[field].kind == "typed"
