"""The live eval entry point, tested offline: its refusal paths and the whole logic of every batch.

Nothing here can reach a live model: the root conftest forces mock mode, removes the key and sets
``MDE_FORBID_LIVE=1``; the batches run on mock stacks (zero cost) through the same code the lead's
live run uses, and ``main`` is exercised with an injected environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from evals.graders.metrics import SessionMetrics, aggregate
from evals.graders.sufficiency import offline_probe_client
from evals.harness.stack import NetworkGuard, StackOptions, build_stack
from evals.runners import live_batch as lb
from evals.runners.mock_batch import render_markdown
from mde_contracts.gateway import BudgetStatus
from mde_core.clock import FakeClock
from mde_core.money import usd_to_micros
from mde_core.settings import Settings
from mde_llm_gateway import InProcessGatewayClient, create_gateway
from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers.mock import MockProvider
from mde_prompts.registry import PromptRegistry

pytestmark = pytest.mark.e2e

LIVE_ENV: Mapping[str, str] = {"LLM_MODE": "live"}


def status(spent_usd: str = "0", reserved_usd: str = "0", tripwire: str = "ok") -> BudgetStatus:
    return BudgetStatus.model_validate(
        {
            "cap_micros": usd_to_micros("6.00"),
            "effective_cap_micros": usd_to_micros("5.70"),
            "spent_micros": usd_to_micros(spent_usd),
            "reserved_micros": usd_to_micros(reserved_usd),
            "tripwire": tripwire,
        }
    )


# --------------------------------------------------------------------------- refusal paths


def test_the_plan_matches_the_budget_table_of_the_brief() -> None:
    assert list(lb.BATCHES) == ["L1", "L2", "L3", "L4"]
    assert {b.id: b.allowance_usd for b in lb.BATCHES.values()} == {
        "L1": "1.20",
        "L2": "1.00",
        "L3": "2.00",
        "L4": "0.50",
    }
    # the allowances plus the L0 smoke (0.30) leave the reserve of at least USD 1.00 under 5.70
    total = sum(b.allowance_micros for b in lb.BATCHES.values()) + usd_to_micros("0.30")
    assert usd_to_micros("5.70") - total >= usd_to_micros("0.70")


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({}, "LLM_MODE=live"),
        ({"LLM_MODE": "mock"}, "LLM_MODE=live"),
        ({**LIVE_ENV, "MDE_FORBID_LIVE": "1"}, "MDE_FORBID_LIVE"),
    ],
)
def test_it_refuses_without_live_mode_and_with_the_test_guard(
    env: Mapping[str, str], needle: str
) -> None:
    reasons = lb.check_live_allowed(env, status(), lb.BATCHES["L1"])
    assert any(needle in r for r in reasons), reasons


def test_it_allows_when_live_mode_is_set_and_the_budget_has_room() -> None:
    assert lb.check_live_allowed(LIVE_ENV, status(), lb.BATCHES["L3"]) == []


def test_it_refuses_at_the_95_percent_tripwire_and_at_80_unless_must_run() -> None:
    at95 = lb.check_live_allowed(LIVE_ENV, status("5.72", tripwire="refuse_95"), lb.BATCHES["L4"])
    assert any("95%" in r for r in at95)
    at80 = status("4.90", tripwire="must_run_only_80")
    assert any("80%" in r for r in lb.check_live_allowed(LIVE_ENV, at80, lb.BATCHES["L4"]))
    must = lb.check_live_allowed(LIVE_ENV, at80, lb.BATCHES["L4"], must_run=True)
    assert not any("80%" in r for r in must)  # (the headroom rule may still refuse)


def test_it_refuses_when_the_headroom_is_smaller_than_the_allowance() -> None:
    tight = status("4.80", "0.10")  # 0.80 of room under 5.70
    reasons = lb.check_live_allowed(LIVE_ENV, tight, lb.BATCHES["L3"])  # allowance 2.00
    assert any("headroom" in r and "L3" in r for r in reasons)
    assert lb.check_live_allowed(LIVE_ENV, tight, lb.BATCHES["L4"]) == []  # 0.50 fits


def test_refusal_reasons_never_contain_a_key_value() -> None:
    fake_key = "sk-test-DONOTPRINTDONOTPRINTDONOTPRINT00"  # secret-scan: allow
    env = {**LIVE_ENV, "OPENAI_API_KEY": fake_key}
    reasons = lb.check_live_allowed(env, status("5.80", tripwire="refuse_95"), lb.BATCHES["L1"])
    assert reasons and not any("DONOTPRINT" in r for r in reasons)


def test_main_prints_the_plan_and_runs_nothing_without_a_batch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert lb.main([], env={}) == 0
    out = capsys.readouterr().out
    assert "L1" in out and "L4" in out and "allowance" in out


def test_main_dry_run_needs_no_switches_and_calls_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert lb.main(["--batch", "L2", "--dry-run"], env={}) == 0
    assert "dry run: nothing was called" in capsys.readouterr().out


def test_main_refuses_a_live_batch_without_live_mode(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_BUDGET_LEDGER", str(tmp_path / "ledger.jsonl"))
    assert lb.main(["--batch", "L1"], env={}) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "LLM_MODE=live" in err
    assert not (tmp_path / "ledger.jsonl").exists(), "a refusal must not touch the ledger"


def write_spend(path: Path, usd: str) -> None:
    """Put ``usd`` of recorded spending into a throwaway ledger through the real budget guard."""
    clock = FakeClock()
    guard = BudgetGuard(
        BudgetConfig.load(cap_usd="6.00"),
        Pricing.load(),
        FileLedger(path, clock=clock),
        bills=True,
        clock=clock,
    )
    remaining, n = usd_to_micros(usd), 0
    while remaining > 0:
        chunk = min(remaining, usd_to_micros("0.30"))
        held = guard.reserve(
            [f"test:fake-spend-{n}"], chunk, purpose="must-run:test", model="gpt-6-luna"
        )
        guard.commit_cost(held, chunk)
        remaining -= chunk
        n += 1


def test_main_refuses_when_the_ledger_says_the_budget_is_spent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LLM_BUDGET_LEDGER", str(ledger_path))
    write_spend(ledger_path, "5.40")  # 90% of the cap in a throwaway ledger (no model is called)
    assert lb.main(["--batch", "L4"], env=LIVE_ENV) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "80%" in err and "headroom" in err


# --------------------------------------------------------------------------- the batches, offline


def offline_context(
    batch_id: str, tmp_path: Path, *, spent: list[int] | None = None
) -> lb.BatchContext:
    """A context over mock stacks (the code path the live run uses, zero cost)."""
    gateway = create_gateway(
        Settings(_env_file=None, llm_mode="mock"),  # type: ignore[call-arg]
        PromptRegistry(),
        provider=MockProvider(),
        clock=FakeClock(),
    )
    counter = spent if spent is not None else [0]
    return lb.BatchContext(
        batch=lb.BATCHES[batch_id],
        make_stack=lambda path, options: build_stack(path, options),
        client=InProcessGatewayClient(gateway),
        probe_client=offline_probe_client(),
        spent=lambda: counter[0],
        out_dir=tmp_path / "out",
        quick=True,
    )


async def test_l1_plays_a_persona_up_to_the_brief(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    result = await lb.execute_batch(offline_context("L1", tmp_path))
    [unit] = result["units"]
    assert result["batch"] == "L1" and result["spent_micros"] == 0
    assert unit["state"] == "DISCOVERING" and unit["cards_asked"] == 0  # P01: hi only
    assert unit["problems"] == []


async def test_l2_reports_the_research_of_a_session(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    result = await lb.execute_batch(offline_context("L2", tmp_path))
    [unit] = result["units"]
    assert unit["research_status"] == "ok" and unit["queries"] >= 1 and unit["facts"] >= 1
    assert unit["research_calls"].get("C8.synthesize_research") == 1


async def test_l3_runs_a_full_session_and_records_its_golden_output(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    result = await lb.execute_batch(offline_context("L3", tmp_path))
    [unit] = result["units"]
    assert unit["spec_ready"] is True and unit["validators_clean"] is True
    assert (tmp_path / "out" / f"{unit['journey']}.APP_SPEC.txt").is_file()
    assert result["aggregate"]["reached_spec_ready"] == 1.0


async def test_l4_runs_injections_the_probe_and_the_critic_check(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    result = await lb.execute_batch(offline_context("L4", tmp_path))
    assert result["injection_pass_rate"] == 1.0 and len(result["injections"]) == 1
    [probe] = result["probes"]
    assert probe["accuracy"] == 1.0 and probe["screens"]
    assert result["critic"]["removed_screens"] >= 1 and result["critic"]["verdict"] in (
        "pass",
        "fix",
    )


async def test_a_batch_stops_when_its_allowance_is_spent(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    clock = [0]
    ctx = offline_context("L1", tmp_path, spent=clock)
    ctx.quick = False
    real = ctx.make_stack

    def costly(path: Path, options: StackOptions):  # type: ignore[no-untyped-def]
        clock[0] += ctx.batch.allowance_micros  # the first unit "spends" the whole allowance
        return real(path, options)

    ctx.make_stack = costly
    result = await lb.execute_batch(ctx)
    assert result["stopped_early"] is True
    assert len(result["units"]) == 1
    assert any("allowance is spent" in n for n in result["notes"])


def test_main_runs_a_batch_end_to_end_on_a_mock_gateway(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    socket_guard: NetworkGuard,
) -> None:
    """With live mode injected but the process still in mock mode (the root conftest), ``main``
    gets all the way through a batch, prints the ledger delta and writes the raw result."""
    monkeypatch.setenv("LLM_BUDGET_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(lb, "RAW", tmp_path / "raw")
    code = lb.main(["--batch", "L1", "--quick"], env=LIVE_ENV)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "ledger delta USD 0.0000" in out and "just budget" in out
    assert (tmp_path / "raw" / "live-L1.json").is_file()


# --------------------------------------------------------------------------- the report


def metrics(name: str, **over: object) -> SessionMetrics:
    base: dict[str, object] = {
        "name": name,
        "mode": "guided",
        "cards_asked": 7,
        "cards_shown": 8,
        "assumed_slots": 4,
        "filled_slots": 16,
        "first_pass": True,
        "repair_rounds": 0,
        "proposals_applied": 30,
        "proposals_dropped": 2,
        "screens": 24,
        "tokens_per_slice_mean": 3000.0,
        "tokens_per_slice_max": 6000,
        "llm_calls": 25,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_micros": 0,
        "latency_ms": 12000,
        "research_additions": 6,
        "research_accepted": 6,
        "validators_clean": True,
        "spec_ready": True,
        "probe_accuracy": 1.0,
    }
    return SessionMetrics(**{**base, **over})  # type: ignore[arg-type]


def test_aggregate_computes_the_documented_rates() -> None:
    sessions = [
        metrics("a"),
        metrics("b", cards_asked=11, first_pass=False, repair_rounds=2, proposals_dropped=10),
        metrics("c", mode="enterprise", cards_asked=19),
    ]
    agg = aggregate(sessions)
    cards = agg["cards_per_guided_session"]
    assert isinstance(cards, dict)
    assert cards["mean"] == 9.0 and cards["max"] == 11  # enterprise sessions are not counted
    assert cards["within_cap"] == 0.5 and cards["at_or_under_target"] == 0.5
    assert agg["first_pass_validator_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert agg["assumption_rate"] == 0.25
    proposals = agg["proposals"]
    assert isinstance(proposals, dict) and proposals["applied"] == 90 and proposals["dropped"] == 14
    assert agg["sufficiency_probe_accuracy"] == 1.0


def test_aggregate_of_nothing_says_none_not_zero() -> None:
    agg = aggregate([])
    assert agg["assumption_rate"] is None and agg["first_pass_validator_rate"] is None


def test_the_markdown_report_has_no_wall_clock_and_names_its_honesty_notes() -> None:
    result = {
        "run": {"id": "unit", "mode": "mock", "sections": ["briefs"]},
        "metrics": aggregate([metrics("a")]) | {"injection_pass_rate": 1.0},
        "personas": [],
        "injections": [],
        "probe": [],
        "datasets": {"personas": 10},
        "honesty": ["Mock mode: cost is zero."],
    }
    text = render_markdown(result)
    assert text.startswith("# Eval report: unit (mock mode)")
    assert "Mock mode: cost is zero." in text and "Sufficiency probe accuracy" in text
    assert "2026" not in text and "T12:" not in text  # no timestamps
