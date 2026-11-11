"""Security hardening of the budget (review items M4, M5, M6): the cap is not caller-influenced,
accounting fails closed, the ledger path does not depend on the working directory."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from mde_contracts.gateway import Usage
from mde_core.clock import FakeClock
from mde_core.errors import BudgetExceeded, ProviderError
from mde_core.paths import anchor_to_main_repo
from mde_core.settings import Settings
from mde_llm_gateway.budget import (
    HARD_CAP_MICROS,
    HARD_CAP_USD,
    HARD_EFFECTIVE_CAP_MICROS,
    BudgetConfig,
    BudgetGuard,
)
from mde_llm_gateway.ledger import FileLedger, LedgerEvent, MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers import (
    NO_USAGE,
    ProviderCallError,
    ScriptedProvider,
    ScriptedResponse,
)
from mde_llm_gateway.testing import GuardFactory, budget_config_for_tests
from mde_llm_gateway.testing_specs import ECHO_SPEC, EchoIn, EchoOut, build_test_gateway, make_ctx

MODEL = "gpt-6-luna"
MUST = "must-run:test"
GOOD = EchoOut(answer="hello there", confidence=0.5)
Q = EchoIn(question="what is 2+2?")


# ---------------------------------------------------------------------------- M4 + M5: the cap
def test_ledger_anchored_and_cap_clamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """The default ledger is the same file from every working directory, and ``LLM_BUDGET_USD``
    cannot lift the USD 8.00 ceiling."""
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    other = tmp_path / "another"
    other.mkdir()

    monkeypatch.chdir(elsewhere)
    first = Settings(_env_file=None).llm_budget_ledger  # type: ignore[call-arg]
    monkeypatch.chdir(other)
    second = Settings(_env_file=None).llm_budget_ledger  # type: ignore[call-arg]
    assert first == second and first.is_absolute()
    assert first.parts[-3:] == ("data", ".budget", "ledger.jsonl")
    assert not str(first).startswith(str(tmp_path))  # not relative to either cwd
    assert anchor_to_main_repo("data/.budget/ledger.jsonl") == first
    # the env var is anchored the same way, an absolute path is left alone
    monkeypatch.setenv("LLM_BUDGET_LEDGER", "data/.budget/other.jsonl")
    assert Settings(_env_file=None).llm_budget_ledger == first.with_name("other.jsonl")  # type: ignore[call-arg]
    absolute = tmp_path / "mine.jsonl"
    assert Settings(_env_file=None, llm_budget_ledger=absolute).llm_budget_ledger == absolute  # type: ignore[call-arg]

    # the cap: 60 is clamped to the hard 8.00 with a warning; the effective cap stays at 7.60 (D-125)
    assert Decimal("8.00") == HARD_CAP_USD and HARD_EFFECTIVE_CAP_MICROS == 7_600_000
    with capture_logs() as logs:
        config = BudgetConfig.load(cap_usd=Decimal("60"))
    assert config.cap_micros == HARD_CAP_MICROS == 8_000_000
    assert config.effective_cap_micros == 7_600_000
    warned = [e for e in logs if e["event"] == "budget_cap_clamped"]
    assert len(warned) == 1 and warned[0]["requested_micros"] == 60_000_000
    # a budget file that sets the effective fraction to 100% does not lift the effective cap either
    full = BudgetConfig.from_dict({"effective_cap_fraction": "1.0"}, cap_usd=Decimal("8.00"))
    assert full.effective_cap_micros == 7_600_000
    # and the guard really refuses past 7.60 whatever was asked for
    guard = BudgetGuard(
        budget_config_for_tests("60"), Pricing.load(), MemoryLedger(clock=clock), bills=True
    )
    guard.reserve(["adhoc:a"], 7_600_000, purpose=MUST, model=MODEL)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 1, purpose=MUST, model=MODEL)
    # smaller caps still work (the clamp only lowers); 95% of 6.00 (the default cap in force) stays 5.70
    assert BudgetConfig.load(cap_usd=Decimal("2.00")).cap_micros == 2_000_000
    assert BudgetConfig.load(cap_usd=Decimal("6.00")).effective_cap_micros == 5_700_000


def test_unknown_scope_kinds_are_refused_before_anything_is_written(
    make_guard: GuardFactory,
) -> None:
    ledger = MemoryLedger()
    guard = make_guard(ledger=ledger)
    for bad in ("x:1", "nokind", ":empty", "Session:1", " session:1", "sessions:1"):
        with pytest.raises(BudgetExceeded) as exc:
            guard.reserve([bad], 100, model=MODEL)
        assert exc.value.details["reason"] == "unknown_scope_kind"
    with pytest.raises(BudgetExceeded):  # one bad scope among good ones refuses the whole call
        guard.reserve(["session:ok", "x:1"], 100, model=MODEL)
    assert ledger.lines == []
    for good in ("session:s1", "research:r1", "eval:L1", "test:t1"):
        guard.reserve([good], 100, model=MODEL)


def test_scope_kinds_can_only_be_extended_through_the_budget_file() -> None:
    config = BudgetConfig.from_dict(
        {"scope_caps_usd": {"session": "0.50", "batch": "0.10"}, "scope_kinds": ["nightly"]},
        cap_usd=Decimal("6.00"),
    )
    assert {"session", "research", "eval", "test", "batch", "nightly"} <= config.scope_kinds
    config.check_scopes(["batch:1", "nightly:x"])
    with pytest.raises(BudgetExceeded):
        config.check_scopes(["other:1"])
    assert config.scope_cap("batch:1") == 100_000  # the kind default applies


def test_must_run_is_still_honoured_in_process(make_guard: GuardFactory) -> None:
    """Trusted in-process code keeps the privilege (the HTTP API strips it from callers)."""
    guard = make_guard()
    rsv = guard.reserve(["adhoc:fill"], 4_800_000, purpose=MUST, model=MODEL)
    guard.commit_cost(rsv, 4_800_000)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 10, purpose="api:must-run:x", model=MODEL)
    guard.reserve(["adhoc:a"], 10, purpose=MUST, model=MODEL)


# ---------------------------------------------------------------------------- M6: fail-closed
class FlakyLedger(MemoryLedger):
    """A memory ledger whose writes of the given event kinds fail ``times`` times."""

    def __init__(self, *, fail: set[str], times: int, clock: FakeClock) -> None:
        super().__init__(clock=clock)
        self.fail, self.times = fail, times

    def _write(self, event: LedgerEvent) -> None:
        if event.event in self.fail and self.times > 0:
            self.times -= 1
            raise OSError("disk full")
        super()._write(event)


def _live(clock: FakeClock) -> ScriptedProvider:
    return ScriptedProvider(mode="live", billable=True, clock=clock)


async def test_commit_failure_books_worst_case(clock: FakeClock) -> None:
    """A commit that fails (ledger write error) books the reservation's worst case instead of
    leaving it open; a negative usage value is rejected and booked at worst case too."""
    # 1. the first commit write fails once: the retry books the worst case
    ledger = FlakyLedger(fail={"commit"}, times=1, clock=clock)
    provider = _live(clock).queue(
        "T1.echo", ScriptedResponse(output=GOOD, usage=Usage(input_tokens=100, output_tokens=10))
    )
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    with capture_logs() as logs:
        result = await gw.run(ECHO_SPEC, Q, make_ctx())
    assert result.output == GOOD  # the paid answer is not thrown away
    events = ledger.events()
    assert [e.event for e in events] == ["reserve", "commit"]
    worst = events[0].worst_case_micros or 0
    assert (events[1].cost_micros or 0) == worst > 0  # NOT the cheap real cost
    assert gw.budget_status().spent_micros == worst and gw.budget_status().reserved_micros == 0
    assert any(e["event"] == "llm_commit_failed" and e["log_level"] == "critical" for e in logs)
    assert result.usage.cost_micros == worst

    # 2. negative usage from the provider: rejected, booked at worst case, token counts zeroed
    ledger = MemoryLedger(clock=clock)
    provider = _live(clock).queue(
        "T1.echo",
        ScriptedResponse(output=GOOD, usage=Usage(input_tokens=-5, output_tokens=10)),
    )
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    result = await gw.run(ECHO_SPEC, Q, make_ctx())
    events = ledger.events()
    assert [e.event for e in events] == ["reserve", "commit"]
    assert (events[1].cost_micros or 0) == (events[0].worst_case_micros or 0) > 0
    assert result.usage.input_tokens == 0 and result.usage.output_tokens == 0

    # 3. a response without usage is the same story (existing rule, kept)
    ledger = MemoryLedger(clock=clock)
    provider = _live(clock).queue("T1.echo", ScriptedResponse(output=GOOD, usage=NO_USAGE))
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    await gw.run(ECHO_SPEC, Q, make_ctx())
    assert (ledger.events()[1].cost_micros or 0) == (ledger.events()[0].worst_case_micros or 0)

    # 4. usage reported with a failed response and a failing commit: worst case as well
    ledger = FlakyLedger(fail={"commit"}, times=1, clock=clock)
    provider = _live(clock).queue(
        "T1.echo", ProviderCallError("boom", status=500, usage=Usage(input_tokens=2000))
    )
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    with pytest.raises(ProviderError):
        await gw.run(ECHO_SPEC, Q, make_ctx())
    events = ledger.events()
    assert (events[1].cost_micros or 0) == (events[0].worst_case_micros or 0)


async def test_unwritable_ledger_keeps_the_reservation_and_it_counts_as_spent_when_stale(
    clock: FakeClock,
) -> None:
    """If even the worst-case booking cannot be written the call fails closed, the reservation stays
    open, and once it is stale the ledger counts it as spent (never as released)."""
    ledger = FlakyLedger(fail={"commit"}, times=99, clock=clock)
    provider = _live(clock).queue(
        "T1.echo", ScriptedResponse(output=GOOD, usage=Usage(input_tokens=10))
    )
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    with pytest.raises(OSError):
        await gw.run(ECHO_SPEC, Q, make_ctx())
    worst = ledger.events()[0].worst_case_micros or 0
    status = gw.budget_status()
    assert (status.reserved_micros, status.spent_micros) == (worst, 0)  # still held: counted
    clock.advance(601)
    status = gw.budget_status()
    assert (status.reserved_micros, status.spent_micros) == (0, worst)  # stale: burnt, not freed


async def test_a_failed_settlement_never_masks_a_cancellation(clock: FakeClock) -> None:
    import asyncio

    started = asyncio.Event()

    class Hanging:
        name = "hanging"
        mode = "live"
        billable = True

        async def complete(self, request: object) -> object:
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    ledger = FlakyLedger(fail={"commit"}, times=99, clock=clock)
    gw = build_test_gateway(Hanging(), clock=clock, ledger=ledger)  # type: ignore[arg-type]
    task = asyncio.create_task(gw.run(ECHO_SPEC, Q, make_ctx()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gw.budget_status().reserved_micros > 0  # left open, counted


def test_the_guard_books_worst_case_for_unpriceable_usage(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:s"], 5_000, model=MODEL, mode="live")
    with capture_logs() as logs:
        recorded = guard.commit(rsv, Usage(input_tokens=100, output_tokens=-1))
    assert recorded.cost_micros == 5_000 and recorded.input_tokens == 0
    assert guard.status().spent_micros == 5_000
    assert any(e["event"] == "budget_usage_unpriceable_booked_worst_case" for e in logs)
    # an unknown model on the reservation is handled the same way (no price: assume the worst)
    rsv2 = guard.reserve(["session:s"], 3_000, model="gpt-9-nova", mode="live")
    assert guard.commit(rsv2, Usage(input_tokens=10)).cost_micros == 3_000


def test_settings_default_env_is_not_polluted() -> None:
    """Sanity: the hardening tests above must not depend on an operator's LLM_BUDGET_LEDGER."""
    assert os.environ.get("LLM_BUDGET_LEDGER") is None


def test_non_billing_ledger_is_unaffected_by_the_integrity_machinery(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "x" / "ledger.jsonl", guard=False)
    assert ledger.integrity_problem() is None
    assert not (tmp_path / "x").exists()
