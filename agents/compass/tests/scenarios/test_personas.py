"""The ten persona scripts (packages/compass/evals/personas) run offline, and the metric targets they measure.

The hard targets must hold today. The goal targets that the product does not meet yet are strict xfails that
name the bug, so they turn red (and must be un-marked) the day the product meets them.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals"


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("compass_run_personas", EVALS / "run_personas.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return asyncio.run(_load_runner().run())


def test_ten_personas_all_run_to_the_end(report: dict[str, Any]) -> None:
    kinds = {r["kind"] for r in report["rows"]}
    assert kinds == {
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
