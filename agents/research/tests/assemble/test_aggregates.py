"""Platform constraints, the compliance register and the lane decisions are built by code from the items (D-159)."""

from __future__ import annotations

from typing import Any

from support.content import make_item

from mde_research_agent.assemble.decisions import build_decisions
from mde_research_agent.assemble.items import (
    canonical_amount,
    merge_payloads,
    provider_fact_value,
    scalar_variants,
    typed_slot,
)
from mde_research_agent.assemble.platform import build_platform_constraints, capability_id_of
from mde_research_agent.assemble.register import build_register, country_of
from mde_research_agent.catalog import load_catalog

CAT = load_catalog()


# --- group keys and merging: a conflicting scalar never collapses silently -------------------------------------------------


def typed(amount: str, unit: str = "percent", **kw: Any) -> dict[str, Any]:
    return {
        "amount": amount,
        "currency": None,
        "unit": unit,
        "period": None,
        "condition": None,
        **kw,
    }


def test_the_canonical_value_of_a_fee_is_its_amount_and_slot_not_its_wording() -> None:
    a = provider_fact_value({"value": "2.50 percent", "typed": typed("2.50")})
    b = provider_fact_value({"value": "two and a half percent", "typed": typed("2.5")})
    assert a == b
    c = provider_fact_value({"value": "2.9 percent", "typed": typed("2.9")})
    d = provider_fact_value(
        {"value": "2.5 per transaction", "typed": typed("2.5", unit="transaction")}
    )
    assert len({a, c, d}) == 3, "a different amount or a different unit is a different fact"


def test_a_value_without_a_typed_amount_is_compared_by_its_words() -> None:
    assert provider_fact_value({"value": "Test Keys!", "typed": None}) == provider_fact_value(
        {"value": "test keys", "typed": None}
    )
    assert canonical_amount(None) == "" and typed_slot(None) == ("", "", "")


def test_a_different_figure_in_a_merged_scalar_is_kept_as_a_variant() -> None:
    primary = {"fees": "Rs. 0.16 per SMS", "limits": "none stated", "region_fit": "India"}
    others = [{"fees": "Rs. 0.20 per SMS", "limits": "none stated", "region_fit": "all of India"}]
    assert scalar_variants(primary, others) == {"fees": ["Rs. 0.20 per SMS"]}
    # the merge itself still fills only empty scalars: the primary stays, the variant is reported beside it
    assert merge_payloads(primary, others)["fees"] == "Rs. 0.16 per SMS"


def test_wording_differences_without_a_different_figure_are_not_variants() -> None:
    assert (
        scalar_variants({"fees": "Rs. 0.16 per SMS"}, [{"fees": "0.16 rupees for each SMS"}]) == {}
    )
    assert scalar_variants({"fees": "free"}, [{"fees": "no charge"}]) == {}


# --- platform constraints ---------------------------------------------------------------------------------------------------


def constraint(
    iid: str,
    capability: str,
    platform: str | None,
    effect: str | None,
    evidence: str = "official",
    **kw: Any,
):
    return make_item(
        iid,
        "",
        "",
        "",
        kind="platform_constraint",
        evidence=evidence,
        data={
            "capability": capability,
            "platform": platform,
            "rule": f"rule of {iid}",
            "effect_on_build": effect,
            "status": None,
        },
        **kw,
    )


def test_platform_constraints_are_ordered_by_capability_platform_and_evidence() -> None:
    items = [
        constraint("RI-001", "background location", "ios", "needs_permission"),
        constraint("RI-002", "push notifications", "android", None, evidence="single_source"),
        constraint("RI-003", "push notifications", "both", "needs_config_plugin"),
        constraint("RI-004", "push notifications", "android", "needs_permission"),
        constraint("RI-005", "something the file does not list", None, None),
    ]
    rows = build_platform_constraints(items, CAT.capabilities)
    assert [r.item_id for r in rows] == ["RI-003", "RI-004", "RI-002", "RI-001", "RI-005"]
    assert [r.capability for r in rows] == [
        "push_notifications",
        "push_notifications",
        "push_notifications",
        "background_location",
        "something the file does not list",
    ]


def test_an_effect_the_page_did_not_state_stays_null() -> None:
    (row,) = build_platform_constraints(
        [constraint("RI-001", "deep links", "ios", None)], CAT.capabilities
    )
    assert row.effect_on_build is None and row.platform == "ios" and row.evidence == "official"
    assert row.domain == "docs.payco.example"


def test_capability_text_maps_to_the_data_files_ids() -> None:
    assert capability_id_of("Push notifications", CAT.capabilities) == "push_notifications"
    assert capability_id_of("notifications", CAT.capabilities) == "push_notifications"
    assert (
        capability_id_of("in-app purchases and subscriptions", CAT.capabilities) == "store_payments"
    )
    assert capability_id_of("quantum", CAT.capabilities) is None


def test_other_region_items_are_not_listed() -> None:
    assert (
        build_platform_constraints(
            [constraint("RI-001", "deep links", "ios", None, flags=["other_region"])],
            CAT.capabilities,
        )
        == []
    )


# --- the compliance register ------------------------------------------------------------------------------------------------


def note(
    iid: str,
    topic: str,
    *,
    regime: str | None,
    country: str | None,
    region: str,
    evidence: str = "official",
    **extra: Any,
):
    return make_item(
        iid,
        "",
        "",
        "",
        kind="compliance_note",
        evidence=evidence,
        region=region,
        data={
            "topic": topic,
            "authority": "The Regulator",
            "summary": f"summary of {iid}",
            "effective_date": "2025-01-01",
            "regime": regime,
            "country": country,
            "applies_to": ["owner records"],
            "penalty": None,
            "legal_adjacent": True,
            **extra,
        },
    )


def test_the_register_groups_by_country_then_regime_and_puts_global_and_unknown_last() -> None:
    items = [
        note("RI-001", "retention", regime="Data Act", country=None, region="in"),
        note(
            "RI-002",
            "consent",
            regime="Data Act",
            country=None,
            region="in",
            evidence="single_source",
        ),
        note("RI-003", "breach notice", regime="Breach Rules", country=None, region="in"),
        note("RI-004", "store disclosure", regime=None, country=None, region="global"),
        note("RI-005", "privacy", regime="Privacy Law", country="Kenya", region="unknown"),
        note("RI-006", "other", regime="Other Law", country=None, region="ae"),
    ]
    reg = build_register(items)
    assert [c.country for c in reg] == ["ae", "in", "kenya", "global"]
    india = reg[1]
    assert [r.regime for r in india.regimes] == ["Breach Rules", "Data Act"]
    assert [e.item_id for e in india.regimes[1].entries] == ["RI-001", "RI-002"], (
        "best evidence first"
    )
    entry = india.regimes[1].entries[0]
    assert entry.statement == "summary of RI-001" and entry.authority == "The Regulator"
    assert (
        entry.effective_date == "2025-01-01"
        and entry.applies_to == ["owner records"]
        and entry.penalty is None
    )
    assert reg[-1].regimes[0].regime == "store disclosure", (
        "a note without a regime is listed under its topic"
    )


def test_the_country_is_the_items_region_then_the_one_the_page_names() -> None:
    assert country_of(note("RI-001", "t", regime=None, country="Kenya", region="in")) == "in"
    assert (
        country_of(note("RI-002", "t", regime=None, country="Kenya", region="unknown")) == "kenya"
    )
    assert country_of(note("RI-003", "t", regime=None, country=None, region="unknown")) == "unknown"


# --- lane decisions ----------------------------------------------------------------------------------------------------------


def lane_of(items: list, lane: str = "integrations") -> dict[str, list[str]]:
    return {lane: [i.id for i in items]}


def test_a_decision_lists_the_usable_items_and_the_best_label() -> None:
    items = [
        make_item(
            "RI-001", "Payco", "fees", "2.9 percent", typed=typed("2.9"), evidence="corroborated"
        ),
        make_item("RI-002", "Payco", "limits", "1000 a day", evidence="single_source"),
        make_item("RI-003", "Payco", "fees", "9 percent", typed=typed("9"), evidence="unverified"),
    ]
    rows = {r.id: r for r in build_decisions(items, lane_of(items), CAT, [("services", "covered")])}
    terms = rows["IN-TERMS"]
    assert terms.item_ids == ["RI-001", "RI-002"] and terms.unverified_item_ids == ["RI-003"]
    assert terms.best_evidence == "corroborated" and terms.gap is None


def test_a_decision_with_nothing_usable_has_a_gap_with_a_reason() -> None:
    weak = [make_item("RI-001", "Payco", "sandbox", "test keys", evidence="unverified")]
    ran = [("services", "covered"), ("platform", "not_found")]
    rows = {r.id: r for r in build_decisions(weak, lane_of(weak), CAT, ran)}
    assert (
        rows["IN-CONTRACT"].gap is not None and rows["IN-CONTRACT"].gap.reason == "unverified_only"
    )
    assert rows["IN-CONTRACT"].item_ids == [] and rows["IN-CONTRACT"].unverified_item_ids == [
        "RI-001"
    ]
    assert rows["IN-TERMS"].gap is not None and rows["IN-TERMS"].gap.reason == "not_found", (
        "the topics ran and found nothing"
    )
    assert rows["IN-PLATFORM"].gap is not None and rows["IN-PLATFORM"].gap.reason == "not_found"
    assert rows["IN-PLATFORM"].gap.note, "the gap carries the note Spec reads"


def test_gap_reasons_not_run_and_not_planned() -> None:
    queued = {
        r.id: r
        for r in build_decisions([], {}, CAT, [("services", "queued"), ("services", "skipped")])
    }
    assert queued["IN-PROVIDERS"].gap is not None and queued["IN-PROVIDERS"].gap.reason == "not_run"
    assert (
        queued["IN-PLATFORM"].gap is not None and queued["IN-PLATFORM"].gap.reason == "not_planned"
    )


def test_only_main_store_items_of_the_lane_inform_a_decision() -> None:
    items = [make_item("RI-001", "Payco", "fees", "2.9 percent", typed=typed("2.9"))]
    rows = {
        r.id: r
        for r in build_decisions(items, {"integrations": []}, CAT, [("services", "covered")])
    }
    assert rows["IN-TERMS"].item_ids == [] and rows["IN-TERMS"].gap is not None


def test_the_confirm_decision_reads_the_needs_confirm_flag() -> None:
    items = [
        make_item(
            "RI-001", "Payco", "fees", "2.9 percent", typed=typed("2.9"), flags=["needs-confirm"]
        ),
        make_item("RI-002", "Payco", "products", "cards"),
    ]
    lanes = {"verify": [i.id for i in items]}
    rows = {r.id: r for r in build_decisions(items, lanes, CAT, [])}
    assert rows["VF-CONFIRM"].item_ids == ["RI-001"]


def test_every_decision_of_the_data_file_is_listed_once_in_lane_order() -> None:
    rows = build_decisions([], {}, CAT, [])
    ids = [r.id for r in rows]
    assert len(ids) == len(set(ids)) == sum(len(d) for d in CAT.lanes.decisions.values())
    assert [r.lane for r in rows] == sorted((r.lane for r in rows), key=list(CAT.lanes.lanes).index)
