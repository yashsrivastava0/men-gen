"""The content aggregates through a whole run (scripted extraction over the fake web): provider facts become items, items become
dossiers, constraints, a register and lane decisions, and every validator still passes (RV-24..RV-28)."""

from __future__ import annotations

import asyncio
import re

import pytest
from support.content import combine, compliance_extractor, fact_extractor, platform_extractor
from support.testing import atom, extractor, load_fixture, rule_extractor, run_engine

from mde_research_agent.assemble.store import compact_dossier, tokens_of
from mde_research_agent.domain.atoms import payload_from_wire, wire_atom_model


@pytest.fixture(scope="module")
def rich():
    return asyncio.run(
        run_engine(
            load_fixture("standard-produce-marketplace-nashik"),
            overrides={
                "rsch.extract.services": fact_extractor(),
                "rsch.extract.platform": platform_extractor(),
                "rsch.extract.data": combine(rule_extractor(), compliance_extractor()),
            },
        )
    )


def test_the_run_is_clean_and_has_every_new_aggregate(rich) -> None:
    assert rich.violations == [], rich.violations
    r = rich.research
    assert r["schema"] == "research.v5", "the schema id does not change (D-162)"
    assert (
        r["dossiers"]
        and r["platform_constraints"]
        and r["compliance_register"]
        and r["lane_decisions"]
    )
    kinds = {i["kind"] for i in r["items"]}
    assert {"provider_fact", "platform_constraint", "compliance_note"} <= kinds


def test_two_conflicting_fees_stay_two_items_and_the_dossier_field_is_a_conflict_with_both_values(
    rich,
) -> None:
    r = rich.research
    fees = [i for i in r["items"] if i["kind"] == "provider_fact" and i["data"]["field"] == "fees"]
    assert len({i["data"]["typed"]["amount"] for i in fees}) >= 2, (
        "the fake pages state different fees"
    )
    assert len(fees) >= 2, "different amounts are different items, never merged"
    assert all({"conflict", "needs-confirm"} <= set(i["flags"]) for i in fees)
    d = next(d for d in r["dossiers"] if d["fields"]["fees"]["null_reason"] == "conflict")
    f = d["fields"]["fees"]
    assert f["value"] is None
    assert sorted(f["item_ids"]) == sorted(i["id"] for i in fees)
    assert len(f["values"]) >= 2 and all(v["conflict"] for v in f["values"])
    assert any(set(c["item_ids"]) == set(f["item_ids"]) for c in r["conflicts"]), (
        "listed with the other conflicts"
    )
    confirmed = {c["item_id"] for c in r["confirm"]}
    assert set(f["item_ids"]) <= confirmed, "numbers and conflicts go on the owner's confirm list"


def test_dossier_values_point_at_provider_facts_with_their_own_labels(rich) -> None:
    by_id = {i["id"]: i for i in rich.research["items"]}
    for d in rich.research["dossiers"]:
        for key, f in d["fields"].items():
            assert (f["value"] is None) == (f["null_reason"] is not None) or f[
                "evidence"
            ] == "unverified", key
            for v in f["values"]:
                assert by_id[v["item_id"]]["evidence"] == v["evidence"]
        assert d["completeness"]["required"] == len(d["required"])


def test_platform_constraints_carry_the_stated_effect_and_the_source(rich) -> None:
    by_id = {i["id"]: i for i in rich.research["items"]}
    for row in rich.research["platform_constraints"]:
        item = by_id[row["item_id"]]
        assert item["kind"] == "platform_constraint"
        assert row["effect_on_build"] == item["data"]["effect_on_build"]
        assert row["domain"] == item["sources"][0]["domain"]
        assert row["effect_on_build"] in (
            None,
            "needs_development_build",
            "needs_permission",
            "review_risk",
            "needs_config_plugin",
            "none",
        )


def test_every_register_entry_and_every_compliance_note_is_on_the_confirm_list(rich) -> None:
    r = rich.research
    confirmed = {c["item_id"] for c in r["confirm"]}
    notes = {i["id"] for i in r["items"] if i["kind"] == "compliance_note"}
    assert notes <= confirmed
    in_register = {
        e["item_id"] for c in r["compliance_register"] for g in c["regimes"] for e in g["entries"]
    }
    assert in_register == notes


def test_the_confirm_list_carries_money_facts(rich) -> None:
    r = rich.research
    money = {
        i["id"]
        for i in r["items"]
        if i["kind"] == "provider_fact" and (i["data"].get("typed") or {}).get("amount")
    }
    assert money and money <= {c["item_id"] for c in r["confirm"]}
    assert all(i["impact"] == "high" for i in r["items"] if i["id"] in money), (
        "a typed amount makes a provider fact high impact"
    )


def test_lane_decisions_say_what_research_gave_or_the_gap(rich) -> None:
    rows = {d["id"]: d for d in rich.research["lane_decisions"]}
    assert rows["IN-PLATFORM"]["item_ids"] and rows["IN-PLATFORM"]["gap"] is None
    assert rows["IN-TERMS"]["item_ids"] or rows["IN-TERMS"]["gap"]
    for d in rows.values():
        assert bool(d["item_ids"]) != bool(d["gap"]), d["id"]
    slices = rich.research["slices"]
    for d in rows.values():
        assert set(d["item_ids"]) <= set(slices[d["lane"]]["item_ids"]), (
            "main-store items of the lane only"
        )


def test_every_item_names_a_brief_id_or_new(rich) -> None:
    assert all(i["applies_to"] for i in rich.research["items"])
    facts = [i for i in rich.research["items"] if i["kind"] == "provider_fact"]
    assert all(any(a["kind"] == "INT" for a in i["applies_to"]) for i in facts), (
        "a provider fact is indexed by its need"
    )
    assert "payments" in rich.research["by_integration"] or rich.research["dossiers_by_integration"]


def test_the_same_inputs_give_the_same_aggregates() -> None:
    async def once():
        run = await run_engine(
            load_fixture("quick-yoga-studio-bengaluru"),
            overrides={
                "rsch.extract.services": fact_extractor(),
                "rsch.extract.platform": platform_extractor(),
            },
        )
        r = run.research
        return [
            r["dossiers"],
            r["platform_constraints"],
            r["lane_decisions"],
            r["dossiers_by_integration"],
        ]

    assert asyncio.run(once()) == asyncio.run(once())


# --- a wrong typed value from the model is dropped, not published --------------------------------------------------------------


def wrong_typed(inp):  # type: ignore[no-untyped-def]
    out = []
    for m in re.finditer(r"[^.]+start at (\d+(?:\.\d+)?) percent[^.]*\.", inp.passages):
        s = " ".join(m.group(0).split())
        base = {
            "provider": "Payco",
            "need": "payments",
            "field": "fees",
            "value": "a fee",
            "countries": [],
        }
        out.append(
            atom(
                "provider_fact",
                s,
                key="payco fee",
                **base,
                typed={
                    "amount": "77",
                    "currency": None,
                    "unit": "percent",
                    "period": None,
                    "condition": None,
                },
            )
        )
        out.append(
            atom(
                "provider_fact",
                s,
                key="payco fee eur",
                **base,
                typed={
                    "amount": m.group(1),
                    "currency": "EUR",
                    "unit": "percent",
                    "period": None,
                    "condition": None,
                },
            )
        )
        out.append(
            atom(
                "provider_fact",
                s,
                key="payco fee ok",
                **base,
                typed={
                    "amount": m.group(1),
                    "currency": None,
                    "unit": "percent",
                    "period": None,
                    "condition": None,
                },
            )
        )
    return out


def test_a_deliberately_wrong_typed_value_is_rejected_by_code_and_the_right_one_survives() -> None:
    run = asyncio.run(
        run_engine(
            load_fixture("quick-yoga-studio-bengaluru"),
            overrides={"rsch.extract.services": extractor(wrong_typed)},
        )
    )
    facts = [i for i in run.research["items"] if i["kind"] == "provider_fact"]
    assert facts, "the grounded fact survives"
    assert all(
        i["data"]["typed"]["amount"] != "77" and i["data"]["typed"]["currency"] is None
        for i in facts
    )
    assert run.research["dropped"]["invalid"] >= 2, (
        "the wrong amount and the wrong currency were counted"
    )
    assert run.violations == []


def test_the_wire_check_is_the_same_function_the_pipeline_uses() -> None:
    quote = (
        "Fees for payments start at 3 percent and funds settle in 2 business days after capture."
    )
    _, a = atom(
        "provider_fact",
        quote,
        key="k",
        provider="Payco",
        need="payments",
        field="fees",
        value="3 percent",
        countries=[],
        typed={"amount": "4", "currency": None, "unit": None, "period": None, "condition": None},
    )
    with pytest.raises(ValueError):
        payload_from_wire("provider_fact", wire_atom_model("provider_fact").model_validate(a))


def test_the_store_size_counts_the_aggregates_the_compact_view_carries(rich) -> None:
    """Dossiers, platform constraints, the register and the lane decisions are part of what Spec reads, so they are inside
    ``profile.store_tokens`` (and the store plan reserved room for them), not outside it."""
    r = rich.research
    aggregates = tokens_of(
        [
            [compact_dossier(d) for d in r["dossiers"]],
            r["dossiers_by_integration"],
            r["platform_constraints"],
            r["compliance_register"],
            r["lane_decisions"],
        ]
    )
    items = sum(i["tokens_est"] for i in r["items"] if i["store"] == "main")
    assert aggregates > 0 and r["profile"]["store_tokens"] >= items + aggregates * 0.8
    assert rich.violations == []
