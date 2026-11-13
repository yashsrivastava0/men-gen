"""RC-9: the evaluation harness (planted errors, consumer probe): seeded, deterministic, costs reported; offline doubles."""

from __future__ import annotations

import json

from support.testing import load_fixture, run_engine

from mde_research_agent.calls import check as check_call
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.eval.corrupt import KINDS, corrupt
from mde_research_agent.eval.planted import (
    TOPUP_N,
    code_catches,
    estimate,
    run_planted,
    score,
    top_up,
)
from mde_research_agent.eval.probe import brief_from_input, findings_text, run_probe, tasks
from mde_research_agent.ids import CounterIds

ITEM = {
    "id": "atm_1",
    "claim": "The gateway charges a fee of 2.5% on every card payment.",
    "quote": "charges a fee of 2.5% on every card payment",
    "label": "Card fee",
    "data": {"value": "2.5%", "constraints": []},
    "about_region": "in",
    "about_entity": "Acme Pay",
    "target_id": "T-01",
}


# Kinds that need something to change: a currency, a period, a qualifier. ITEM has none of the three.
NEEDS_TEXT = ("currency_swapped", "period_changed", "qualifier_dropped")
ITEM_WITH_MONEY = {
    **ITEM,
    "claim": "Plans start at ₹699 per month with a 7-day free trial.",
    "quote": "Plans start at ₹699/mo + GST, with a 7-day free trial.",
    "data": {},
}


def test_the_checker_only_kinds_change_what_the_number_check_cannot_see() -> None:
    expected = {
        "currency_swapped": "Plans start at $699 per month with a 7-day free trial.",
        "period_changed": "Plans start at ₹699 per year with a 7-day free trial.",
        "qualifier_dropped": "Plans exactly ₹699 per month with a 7-day free trial.",
    }
    for kind, claim in expected.items():
        bad = corrupt(ITEM_WITH_MONEY, kind, 1)
        assert (
            bad is not None and bad["claim"] == claim and bad["quote"] == ITEM_WITH_MONEY["quote"]
        )
        # the figure 699 is still in the quote: only the checker can know the period or the qualifier changed. A swapped currency
        # is the one thing the number check now sees too (D-163: a claim that states a currency needs the same one in the quote).
        caught = code_catches(bad, {"place": "in"})
        assert caught == (["number_not_in_quote"] if kind == "currency_swapped" else [])
        assert (
            corrupt(ITEM, kind, 1) is None
        )  # nothing to change in a claim with no currency, period or qualifier


def test_every_kind_plants_one_change_and_leaves_the_quote_alone() -> None:
    for kind in (k for k in KINDS if k not in NEEDS_TEXT):
        bad = corrupt(ITEM, kind, 1)
        assert (
            bad is not None
            and bad["quote"] == ITEM["quote"]
            and bad["planted"] == kind
            and bad["id"].endswith(kind)
        )
        assert (bad["claim"], bad["about_region"], bad["about_entity"], bad["data"]) != (
            ITEM["claim"],
            ITEM["about_region"],
            ITEM["about_entity"],
            ITEM["data"],
        )
    assert corrupt({**ITEM, "claim": "No figure here."}, "number_changed", 1) is None
    assert set(NEEDS_TEXT) <= set(KINDS) and len(KINDS) == 8
    assert ITEM["claim"].startswith("The gateway")  # the input is never changed


def test_corruption_is_deterministic_for_a_seed_and_differs_across_seeds() -> None:
    a = [corrupt(ITEM_WITH_MONEY, k, 5) for k in KINDS]
    assert a == [corrupt(ITEM_WITH_MONEY, k, 5) for k in KINDS]
    assert any(
        corrupt(ITEM, k, 5) != corrupt(ITEM, k, 6)
        for k in ("number_changed", "region_swapped", "unsupported_attribute_added")
    )


def test_code_catches_a_changed_number_and_another_country_but_not_a_flipped_condition() -> None:
    target = {"place": "in", "entity": "Acme Pay"}
    assert code_catches(ITEM, target) == []
    assert "number_not_in_quote" in code_catches(corrupt(ITEM, "number_changed", 1), target)  # type: ignore[arg-type]
    assert "other_region" in code_catches(corrupt(ITEM, "region_swapped", 1), target)  # type: ignore[arg-type]
    assert "other_entity" in code_catches(corrupt(ITEM, "entity_swapped", 1), target)  # type: ignore[arg-type]
    assert code_catches(corrupt(ITEM, "condition_flipped", 1), target) == []  # type: ignore[arg-type]


async def test_the_planted_test_reports_code_and_checker_separately_and_is_repeatable() -> None:
    run = await run_engine(load_fixture("standard-produce-marketplace-nashik"))
    runner = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    a = await run_planted(runner, run.research, run.evidence, n=12, seed=3)
    runner2 = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    b = await run_planted(
        runner2,
        run.research,
        run.evidence,
        n=12,
        seed=3,
    )
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["clean_items"] > 0 and a["planted"] > 0 and set(a["per_kind"]) == set(KINDS)
    nums = a["per_kind"]["number_changed"]
    assert (
        nums["planted"] == 0 or nums["caught_by_code"] == nums["planted"]
    )  # a changed number is always caught by code
    assert all(
        p["caught_by_code"] + p["caught_by_checker"] + p["missed"] == p["planted"]
        for p in a["per_kind"].values()
    )
    assert (
        "cost_micros" in a and a["thresholds"]["overall"] == 0.90 and isinstance(a["passed"], bool)
    )


async def test_the_probe_scores_both_arms_for_every_task_and_prefers_the_research() -> None:
    run = await run_engine(load_fixture("standard-produce-marketplace-nashik"))
    first_main = next(i["id"] for i in run.research["items"] if i["store"] == "main")
    assert first_main in findings_text(run.research), "the research arm sees the main store"
    runner = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    rep = await run_probe(runner, run.research)
    assert [t["task"] for t in rep["tasks"]] == [t["id"] for t in tasks()] and len(tasks()) == 5
    assert rep["totals"]["B"]["supported"] > rep["totals"]["A"]["supported"]
    assert (
        rep["totals"]["A"]["supported"] == 0 and rep["totals"]["A"]["assumed"] > 0
    )  # without findings: everything assumed
    assert rep["passed"] is True and rep["cost_micros"] >= 0


def test_the_estimate_is_known_before_anything_runs() -> None:
    est = estimate(40)
    assert (
        est["variants_max"] == 40 * len(KINDS)
        and est["clean_items"] == 40
        and est["checker_batches_max"] >= 1
    )


# --- the score: what the report says about the checker, on hand-made verdicts -----------------------------------


def _verdict(atom_id: str, verdict: str) -> tuple[str, check_call.CheckVerdict]:
    return atom_id, check_call.CheckVerdict(id=atom_id, verdict=verdict)  # type: ignore[arg-type]


def _cases(n_clean: int = 10) -> tuple[list[dict], list[dict]]:
    clean = [{"id": f"c{i}", "data": {}} for i in range(n_clean)]
    variants = [
        {"id": f"c{i}~{k}", "planted": k, "data": {}} for i in range(n_clean) for k in KINDS[:3]
    ]
    return clean, variants


def test_a_checker_that_says_partial_to_everything_is_degenerate_not_perfect() -> None:
    clean, variants = _cases()
    verdicts = dict(_verdict(a["id"], "partial") for a in [*clean, *variants])
    rep = score(clean, variants, {}, verdicts, [])
    assert (
        rep["caught_overall"] == 1.0 and rep["clean_wrongly_rejected"] == 0
    )  # the old test passed this checker
    assert rep["clean_flagged_rate"] == 1.0 and rep["discrimination"] == 0.0
    assert rep["degenerate_checker"] is True and rep["passed"] is False


def test_a_checker_that_separates_good_from_planted_passes_and_says_how_it_caught() -> None:
    clean, variants = _cases()
    verdicts = {
        **dict(_verdict(a["id"], "supported") for a in clean),
        **dict(
            _verdict(v["id"], "not_supported" if v["planted"] == KINDS[0] else "partial")
            for v in variants
        ),
    }
    rep = score(clean, variants, {}, verdicts, [])
    assert (
        rep["caught_overall"] == 1.0
        and rep["clean_flagged_rate"] == 0.0
        and rep["discrimination"] == 1.0
    )
    assert rep["degenerate_checker"] is False and rep["passed"] is True
    first, second = rep["per_kind"][KINDS[0]], rep["per_kind"][KINDS[1]]
    assert (first["checker_not_supported"], first["checker_partial"]) == (10, 0)
    assert (second["checker_not_supported"], second["checker_partial"]) == (0, 10)


def test_code_catches_count_toward_the_kind_and_a_missed_kind_fails_the_test() -> None:
    clean, variants = _cases()
    coded = {KINDS[0]: {v["id"] for v in variants if v["planted"] == KINDS[0]}}
    verdicts = dict(_verdict(a["id"], "supported") for a in [*clean, *variants])
    rep = score(clean, variants, coded, verdicts, [])
    assert rep["per_kind"][KINDS[0]]["caught_rate"] == 1.0
    assert rep["per_kind"][KINDS[1]]["caught_rate"] == 0.0 and rep["passed"] is False
    assert rep["needs_second_vendor"] is True


def test_the_partial_recheck_says_how_many_partials_the_checker_would_now_accept() -> None:
    clean, variants = _cases(2)
    recheck = [{"id": f"p{i}", "data": {}} for i in range(4)]
    verdicts = {
        **dict(_verdict(a["id"], "supported") for a in clean),
        **dict(_verdict(v["id"], "partial") for v in variants),
        **dict(
            _verdict(r["id"], x)
            for r, x in zip(
                recheck, ["partial", "partial", "supported", "not_supported"], strict=True
            )
        ),
    }
    rep = score(clean, variants, {}, verdicts, recheck)
    assert rep["partial_recheck"] == {
        "items": 4,
        "still_partial": 2,
        "now_supported": 1,
        "now_not_supported": 1,
    }


def test_the_probe_gets_the_real_brief_from_a_stored_input_and_says_when_it_did_not() -> None:
    inp = {
        "idea": {"summary": "A studio booking app", "category": "booking"},
        "geography": {"cities": ["Porto"], "country": "Portugal"},
        "locales": {"codes": ["pt-PT"]},
        "features": [
            {
                "id": "FTR-001",
                "name": "Book a class",
                "priority": "must",
                "intent": "reserve a place",
            }
        ],
        "roles": [{"name": "Member"}],
        "integrations": [{"name": "Card payments"}],
        "facts": [{"statement": "Classes hold at most 12 people."}],
    }
    text = brief_from_input(inp)
    for needle in (
        "A studio booking app",
        "Porto",
        "pt-PT",
        "Book a class",
        "Member",
        "Card payments",
        "at most 12",
    ):
        assert needle in text
    assert len(brief_from_input({"facts": [{"statement": "x" * 500}] * 400})) <= 12_000


async def test_the_probe_report_records_whether_a_brief_was_given() -> None:
    run = await run_engine(load_fixture("standard-produce-marketplace-nashik"))
    runner = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    assert (await run_probe(runner, run.research))["brief_given"] is False
    runner2 = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    assert (await run_probe(runner2, run.research, brief="Idea: a market"))["brief_given"] is True


def test_a_kind_with_too_few_samples_is_reported_but_not_gated() -> None:
    clean = [{"id": f"c{i}", "data": {}} for i in range(5)]
    big = [{"id": f"b{i}~{KINDS[0]}", "planted": KINDS[0], "data": {}} for i in range(30)]
    few = [{"id": f"f{i}~{KINDS[7]}", "planted": KINDS[7], "data": {}} for i in range(3)]
    verdicts = dict(_verdict(a["id"], "supported") for a in clean)
    verdicts.update(_verdict(v["id"], "partial") for v in big)
    verdicts.update(
        [
            _verdict(few[0]["id"], "partial"),
            _verdict(few[1]["id"], "supported"),
            _verdict(few[2]["id"], "supported"),
        ]
    )
    rep = score(clean, [*big, *few], {}, verdicts, [])
    assert (
        rep["per_kind"][KINDS[7]]["caught_rate"] < 0.5
        and rep["per_kind"][KINDS[7]]["gated"] is False
    )
    assert rep["per_kind"][KINDS[0]]["gated"] is True and rep["passed"] is True
    # the same weak kind with enough samples fails the test
    many = [{"id": f"m{i}~{KINDS[7]}", "planted": KINDS[7], "data": {}} for i in range(10)]
    verdicts.update(_verdict(v["id"], "supported") for v in many)
    assert score(clean, [*big, *many], {}, verdicts, [])["passed"] is False


def _atom(i: int, claim: str) -> dict:
    return {
        "id": f"atm_{i}",
        "item_id": f"RI-{i}",
        "kind": "rule",
        "job": "data",
        "label": "x",
        "claim": claim,
        "quote": "q" * 30,
        "status": "verified",
        "verdict": "supported",
        "data": {},
    }


def test_top_up_adds_items_with_a_price_a_period_or_a_qualifier_and_nothing_else() -> None:
    priced = [_atom(i, f"Plans start at ₹{100 + i} per month.") for i in range(20)]
    plain = [_atom(100 + i, f"The studio opens at nine ({i}).") for i in range(20)]
    research = {"items": [{"id": a["item_id"]} for a in [*priced, *plain]]}
    evidence = {"atoms": [*priced, *plain]}
    extra = top_up(research, evidence, [], seed=1)
    assert len(extra) == TOPUP_N and len({a["id"] for a in extra}) == TOPUP_N
    assert all("₹" in a["claim"] for a in extra)  # a plain claim has nothing to swap
    assert extra == top_up(research, evidence, [], seed=1)  # deterministic
    already = priced[:5]
    again = top_up(research, evidence, already, seed=1)
    assert not ({a["id"] for a in already} & {a["id"] for a in again})


def test_a_region_swap_never_lands_on_the_topics_own_country() -> None:
    item = {**ITEM, "about_region": "unknown"}
    for seed in range(40):
        moved = corrupt(item, "region_swapped", seed, avoid_region="in")
        assert moved is not None and moved["about_region"] not in ("in", "unknown")
        # a swap to the topic's own country would plant no error: code could not (and should not) catch it
        assert code_catches(moved, {"place": "in"}) == ["other_region"]
