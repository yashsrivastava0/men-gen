"""RC-9: the evaluation harness (planted errors, consumer probe): seeded, deterministic, costs reported; offline doubles."""

from __future__ import annotations

import json

from support.testing import load_fixture, run_engine

from mde_research_agent.calls.base import CallRunner
from mde_research_agent.eval.corrupt import KINDS, corrupt
from mde_research_agent.eval.planted import code_catches, estimate, run_planted
from mde_research_agent.eval.probe import findings_text, run_probe, tasks
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


def test_every_kind_plants_one_change_and_leaves_the_quote_alone() -> None:
    for kind in KINDS:
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
    assert ITEM["claim"].startswith("The gateway")  # the input is never changed


def test_corruption_is_deterministic_for_a_seed_and_differs_across_seeds() -> None:
    a = [corrupt(ITEM, k, 5) for k in KINDS]
    assert a == [corrupt(ITEM, k, 5) for k in KINDS]
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
        est["variants_max"] == 200 and est["clean_items"] == 40 and est["checker_batches_max"] >= 1
    )
