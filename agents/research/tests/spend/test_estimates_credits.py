"""Expected provider credits per size class: seeded from the measured Small runs, a retry allowance on the high side,
and the measured median replacing ``mid`` once real runs of that size exist (the range stays)."""

from __future__ import annotations

import math
from typing import Any

from mde_research_agent.inputs.estimates import Estimates

PROVIDERS = ("tavily", "serper")
CLASSES = ("tiny", "small", "medium", "big")


def run(
    size: str, credits: dict[str, int], status: str = "complete", kind: str = "run"
) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": status,
        "total_micros": 100_000,
        "meta": {"size_class": size, "credits": credits},
    }


def test_every_class_has_integer_credits_for_both_providers_in_order() -> None:
    est = Estimates()
    for cls in CLASSES:
        credits = est.for_class(cls)["credits"]
        assert set(credits) == set(PROVIDERS)
        for provider in PROVIDERS:
            c = credits[provider]
            assert all(isinstance(c[k], int) and c[k] > 0 for k in ("low", "mid", "high")), (
                cls,
                provider,
            )
            assert c["low"] <= c["mid"] <= c["high"], (cls, provider)


def test_high_allows_for_retries_billing_a_search_twice() -> None:
    """A search can be billed more than once (a worker retry, the failover's relaxed retry or second opinion): the high
    credit estimate is 1.2 x the high number of searches, rounded up."""
    est = Estimates()
    for cls in CLASSES:
        searches_high = est.for_class(cls)["searches"]["high"]
        for provider in PROVIDERS:
            assert est.for_class(cls)["credits"][provider]["high"] == math.ceil(1.2 * searches_high)


def test_small_is_the_plans_expected_size_until_a_run_of_the_current_code_exists() -> None:
    # 2026-11-13 (D-163, D-168): the old code's 27 to 32 searches grew by the platform topics, the provider follow-ups
    # and the second engine on vendor topics; these are expectations, not measurements
    est = Estimates().for_class("small")
    assert est["searches"] == {"low": 30, "mid": 38, "high": 46}
    assert est["credits"]["tavily"] == {"low": 32, "mid": 42, "high": 56}
    assert est["credits"]["serper"] == {"low": 24, "mid": 30, "high": 56}
    assert est["credits_measured_runs"] == {"tavily": 0, "serper": 0}  # no run in this record yet


def test_the_measured_median_replaces_mid_and_the_range_stays() -> None:
    entries = [
        run("small", {"tavily": 31}),
        run("small", {"tavily": 36}),
        run("small", {"tavily": 33, "serper": 2}),
    ]
    est = Estimates().for_class("small", entries)
    assert est["credits"]["tavily"] == {"low": 31, "mid": 33, "high": 56}
    assert est["credits"]["serper"]["mid"] == 2 and est["credits"]["serper"]["low"] == 2
    assert est["credits_measured_runs"] == {"tavily": 3, "serper": 1}


def test_a_measured_run_outside_the_range_widens_it() -> None:
    est = Estimates().for_class("small", [run("small", {"tavily": 70})])
    assert est["credits"]["tavily"] == {"low": 32, "mid": 70, "high": 70}


def test_only_finished_runs_of_the_same_size_and_provider_count() -> None:
    entries = [
        run("small", {"tavily": 90}, status="failed"),
        run("small", {"tavily": 90}, status="open"),
        run("medium", {"tavily": 90}),
        run("small", {"tavily": 90}, kind="ping"),
        run("small", {"tavily": True}),  # type: ignore[dict-item]
        run("small", {"tavily": 0}),
        {"kind": "run", "status": "complete", "total_micros": 1, "meta": {"size_class": "small"}},
        {"kind": "run", "status": "complete", "total_micros": 1},
    ]
    est = Estimates().for_class("small", entries)
    assert est["credits"]["tavily"] == {"low": 32, "mid": 42, "high": 56}
    assert est["credits_measured_runs"] == {"tavily": 0, "serper": 0}


def test_credits_never_change_the_money_estimate() -> None:
    plain = Estimates().for_class("small")
    entries = [run("small", {"tavily": 31})]
    with_credits = Estimates().for_class("small", entries)
    assert with_credits["usd_micros"]["mid"] == 100_000  # from total_micros, as before
    assert plain["usd_micros"] == {"low": 300000, "mid": 420000, "high": 600000}


def test_runs_recorded_before_credits_were_tracked_are_not_measurements_of_the_money_either() -> (
    None
):
    old = {
        "kind": "run",
        "status": "complete",
        "total_micros": 75_000,
        "meta": {"size_class": "small"},
    }
    est = Estimates().for_class("small", [old])
    assert est["usd_micros"]["mid"] == 420000 and est["basis"] == "estimate"
