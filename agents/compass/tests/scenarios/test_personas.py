"""The ten persona scripts (agents/compass/evals/personas) run offline on both question engines, and the metric
targets they measure.

Legacy: the hard targets must hold today; goal targets the product does not meet yet are strict xfails that name
the bug. Planner: the question targets are the interview budgets (Quick 5, Standard 12, Thorough 25) and the
section-6 measures of build prompt 02; a target the planner path misses today is a strict xfail with its BUG or
GAP number, so it turns red (and must be un-marked) the day it is met.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals"
KINDS = {
    "silent",
    "chatty",
    "confused",
    "expert",
    "hinglish",
    "enterprise",
    "voice",
    "adversarial",
    "returns",
    "change_heavy",
}


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("compass_run_personas", EVALS / "run_personas.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return asyncio.run(_load_runner().run())


@pytest.fixture(scope="module")
def planner_report() -> dict[str, Any]:
    return asyncio.run(_load_runner().run(engine="planner"))


# ------------------------------------------------------------------------------------------ legacy engine
def test_ten_personas_all_run_to_the_end(report: dict[str, Any]) -> None:
    assert {r["kind"] for r in report["rows"]} == KINDS
    assert report["metrics"]["scripts_failed"] == []
    assert report["metrics"]["reached_review_pct"] == 100.0


@pytest.mark.parametrize(
    "target",
    [
        "locked_without_evidence_zero",
        "express_le_5",
        "questions_max_le_10",
        "all_three_probes_pass_before_gate1",
        "hidden_instructions_100_ignored",
        "turns_with_errors_zero",
        "cards_valid_or_fallback_100",
        "assumptions_unchanged_ge_80",
    ],
)
def test_hard_targets_hold(report: dict[str, Any], target: str) -> None:
    assert report["targets"][target], report["metrics"]


def test_report_has_the_documented_metrics(report: dict[str, Any]) -> None:
    m = report["metrics"]
    assert {"median", "max", "express_max"} <= set(m["questions_per_brief"])
    assert {"tap_share_pct", "custom_share_pct"} <= set(m["answers"])
    assert {"pass_rate_pct", "all_three_pass_pct"} <= set(m["handoff_probes"])
    assert {"fallback_rate_pct", "generated_valid_pct"} <= set(m["cards"])
    assert m["hidden_instruction_tests"] >= 3


GOALS = {
    # median 8 against 7: the one lever left is the must-confirm policy of auth.account_model (owner decision)
    "questions_median_le_7": "BUG-18",
}


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(
            t, marks=pytest.mark.xfail(strict=True, reason=f"{bug}: see the scenario of that id")
        )
        for t, bug in GOALS.items()
    ],
)
def test_goal_targets_not_met_yet(report: dict[str, Any], target: str) -> None:
    assert report["targets"][target], report["metrics"]


# ----------------------------------------------------------------------------------------- planner engine
def test_planner_personas_all_run_to_the_end(planner_report: dict[str, Any]) -> None:
    assert planner_report["engine"] == "planner"
    assert {r["kind"] for r in planner_report["rows"]} == KINDS
    assert planner_report["metrics"]["scripts_failed"] == []
    assert planner_report["metrics"]["reached_review_pct"] == 100.0


@pytest.mark.parametrize(
    "target",
    [
        "locked_without_evidence_zero",
        "all_three_probes_pass_before_gate1",
        "hidden_instructions_100_ignored",
        "turns_with_errors_zero",
        "cards_valid_or_fallback_100",
        "assumptions_unchanged_ge_80",
        "project_term_share_ge_90",
        "fact_coverage_100",
        "quote_verification_100",
        "followup_vague_ge_90",
        "followup_specific_zero",
        # BUG-24 (a flagged fallback batch repeats the registry cards of an earlier fallback verbatim) is NOT fixed:
        # the mock planner now marks a single-choice question about a list need with a domain topic (type_fit), which
        # changed the ten personas' fallbacks, so the metric holds for the moment. Re-pin it if it comes back.
        "zero_repeated_questions",
    ],
)
def test_planner_hard_targets_hold(planner_report: dict[str, Any], target: str) -> None:
    assert planner_report["targets"][target], planner_report["section6"]


PLANNER_GOALS = {
    "questions_within_budget": (
        "BUG-19",
        "follow-ups planned while batch questions wait, and handoff-gap questions, are not charged to "
        "the budget (Quick 6 of 5, Standard 13 to 16 of 12 and 28 for the answer-all expert, Thorough 27 of 25)",
    ),
    "fallback_count_zero": (
        "GAP-1",
        "the mock planner no longer re-proposes a need an earlier batch asked (18 fallbacks across the ten mock "
        "personas before, 13 now) but still ends in the flagged fallback when its fixed wording meets the "
        "validators; the zero-fallback target is for scripted and live planners",
    ),
}


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=f"{bug}: {why}"))
        for t, (bug, why) in PLANNER_GOALS.items()
    ],
)
def test_planner_goal_targets_not_met_yet(planner_report: dict[str, Any], target: str) -> None:
    assert planner_report["targets"][target], planner_report["section6"]


def test_planner_report_has_the_section_six_measures(planner_report: dict[str, Any]) -> None:
    s6 = planner_report["section6"]
    assert {
        "questions_by_budget",
        "project_term_share",
        "repeated_questions",
        "fact_coverage",
        "quote_violations",
        "fallback_count",
        "followup_rate_vague",
        "followup_rate_specific",
        "needle_present",
    } <= set(s6)
    assert set(s6["questions_by_budget"]) == {"quick", "standard", "thorough"}
    assert s6["followup_rate_vague"] == 1.0 and s6["followup_rate_specific"] == 0.0
    assert "questions_median_le_7" not in planner_report["targets"]
