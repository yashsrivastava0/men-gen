"""Budget guard math (brief 7.9). Boundaries are exact: micro-dollars, no floats."""

from __future__ import annotations

from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from mde_contracts.gateway import Usage
from mde_core.errors import BudgetExceeded, ConfigError, UnknownModelPrice
from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
from mde_llm_gateway.ledger import MemoryLedger
from mde_llm_gateway.testing import GuardFactory, usd

MODEL = "gpt-6-luna"
MUST = "must-run:test"


def _spend(guard: BudgetGuard, micros: int, scope: str = "adhoc:fill") -> None:
    """Record real spend of exactly ``micros`` without touching tripwire rules."""
    rsv = guard.reserve([scope], micros, purpose=MUST, model=MODEL)
    guard.commit_cost(rsv, micros)


# ---------------------------------------------------------------------------- config
def test_effective_cap_is_95_percent_of_the_cap(make_guard: GuardFactory) -> None:
    guard = make_guard("6.00")
    assert guard.config.cap_micros == 6_000_000
    assert guard.config.effective_cap_micros == 5_700_000


def test_default_scope_caps_come_from_budget_yaml(make_guard: GuardFactory) -> None:
    cfg = make_guard().config
    assert cfg.scope_cap("session:ses_1") == usd("1.00")
    assert cfg.scope_cap("research:run_1") == usd("0.30")
    assert cfg.scope_cap("eval:L1") == usd("1.50")
    assert cfg.scope_cap("test:foo") == usd("0.30")
    assert cfg.scope_cap("adhoc:whatever") is None


def test_config_rejects_nonsense() -> None:
    with pytest.raises(ConfigError):
        BudgetConfig(cap_micros=0)
    with pytest.raises(ConfigError):
        BudgetConfig(cap_micros=1, effective_fraction=Decimal("1.5"))
    with pytest.raises(ConfigError):
        BudgetConfig(cap_micros=1, warn_fraction=Decimal("0.9"), must_run_fraction=Decimal("0.5"))


def test_exact_scope_override_wins_over_kind_default() -> None:
    cfg = BudgetConfig.from_dict(
        {"scope_overrides_usd": {"eval:L1": "1.20"}}, cap_usd=Decimal("6.00")
    )
    assert cfg.scope_cap("eval:L1") == usd("1.20")
    assert cfg.scope_cap("eval:other") == usd("1.50")


# ---------------------------------------------------------------------------- global cap boundary
def test_boundary_exactly_at_effective_cap_is_allowed_one_micro_over_is_refused(
    make_guard: GuardFactory,
) -> None:
    guard = make_guard()
    _spend(guard, 4_000_000)  # 66.7%: must-run territory only
    guard.reserve(["adhoc:a"], 1_700_000, purpose=MUST, model=MODEL)  # 4.0 + 1.7 == 5.70 exactly
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 1, purpose=MUST, model=MODEL)


def test_one_micro_over_the_effective_cap_is_refused(make_guard: GuardFactory) -> None:
    guard = make_guard()
    _spend(guard, 4_000_000)
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(["adhoc:a"], 1_700_001, purpose=MUST, model=MODEL)
    assert exc.value.details["scope"] == "global"


def test_reserved_amounts_count_against_the_cap(make_guard: GuardFactory) -> None:
    guard = make_guard()
    first = guard.reserve(["adhoc:a"], 3_000_000, purpose=MUST, model=MODEL)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:b"], 2_700_001, purpose=MUST, model=MODEL)
    guard.reserve(["adhoc:b"], 2_700_000, purpose=MUST, model=MODEL)
    assert guard.status().reserved_micros == 5_700_000
    assert not first.closed


def test_release_frees_the_budget(make_guard: GuardFactory) -> None:
    guard = make_guard()
    held = guard.reserve(["adhoc:a"], 5_000_000, purpose=MUST, model=MODEL)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 1_000_000, purpose=MUST, model=MODEL)
    guard.release(held)
    guard.reserve(["adhoc:a"], 1_000_000, purpose=MUST, model=MODEL)
    status = guard.status()
    assert status.spent_micros == 0
    assert status.reserved_micros == 1_000_000


def test_a_refused_reserve_leaves_no_trace(make_guard: GuardFactory) -> None:
    ledger = MemoryLedger()
    guard = make_guard(ledger=ledger)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 10_000_000, purpose=MUST, model=MODEL)
    assert ledger.lines == []
    assert guard.status().reserved_micros == 0


# ---------------------------------------------------------------------------- scoped caps
def test_session_scope_cap_boundary(make_guard: GuardFactory) -> None:
    guard = make_guard()
    guard.reserve(["session:ses_1"], 900_000, model=MODEL)
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(["session:ses_1"], 100_001, model=MODEL)
    assert exc.value.details["scope"] == "session:ses_1"
    guard.reserve(["session:ses_1"], 100_000, model=MODEL)  # exactly 1.00
    guard.reserve(["session:ses_2"], 500_000, model=MODEL)  # other session unaffected


def test_scoped_cap_counts_real_spend_not_just_reservations(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:ses_1"], 800_000, model=MODEL)
    guard.commit_cost(rsv, 800_000)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["session:ses_1"], 200_001, model=MODEL)
    guard.reserve(["session:ses_1"], 200_000, model=MODEL)


def test_nested_scopes_each_apply_and_global_is_counted_once(make_guard: GuardFactory) -> None:
    guard = make_guard()
    scopes = ["session:ses_1", "research:run_1"]
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(scopes, 300_001, model=MODEL)  # research cap 0.30 trips first
    assert exc.value.details["scope"] == "research:run_1"
    rsv = guard.reserve(scopes, 300_000, model=MODEL)
    guard.commit_cost(rsv, 250_000)
    status = guard.status()
    assert status.spent_micros == 250_000  # global total counts the cost once
    assert status.by_scope == {"session:ses_1": 250_000, "research:run_1": 250_000}
    # The session still has 0.25 left; the research run has 0.05.
    with pytest.raises(BudgetExceeded):
        guard.reserve(scopes, 50_001, model=MODEL)
    guard.reserve(["session:ses_1"], 250_000, model=MODEL)


def test_duplicate_scopes_are_counted_once(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:a", "session:a"], 100_000, model=MODEL)
    guard.commit_cost(rsv, 100_000)
    assert guard.status().by_scope == {"session:a": 100_000}


def test_reserve_validates_arguments(make_guard: GuardFactory) -> None:
    guard = make_guard()
    with pytest.raises(ValueError):
        guard.reserve([], 1)
    with pytest.raises(ValueError):
        guard.reserve(["adhoc:a"], -1)


# ---------------------------------------------------------------------------- commit / release
def test_commit_records_the_real_cost_from_usage(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:s"], 10_000, model=MODEL, mode="live")
    recorded = guard.commit(rsv, Usage(input_tokens=1000, output_tokens=500, tool_calls=0))
    assert recorded.cost_micros == 100 + 250
    status = guard.status()
    assert status.spent_micros == 350
    assert status.reserved_micros == 0
    assert status.live_calls == 1


def test_a_failing_ledger_write_on_commit_is_logged_critically_and_fails_closed(
    make_guard: GuardFactory,
) -> None:
    ledger = MemoryLedger()
    guard = make_guard(ledger=ledger)
    rsv = guard.reserve(["adhoc:a"], 1000, model=MODEL, mode="live", call_id="call_x")

    def disk_full(_event: object) -> None:
        raise OSError("disk full")

    ledger._write = disk_full  # type: ignore[method-assign,assignment]
    with capture_logs() as logs, pytest.raises(OSError):
        guard.commit_cost(rsv, 500)
    critical = [e for e in logs if e["event"] == "budget_commit_failed"]
    assert len(critical) == 1 and critical[0]["log_level"] == "critical"
    assert critical[0]["cost_micros"] == 500 and critical[0]["call_id"] == "call_x"
    assert rsv.closed is False  # the reservation stays open (and expires after its ttl)


def test_commit_includes_web_search_fee(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["research:r"], 50_000, model=MODEL)
    recorded = guard.commit(rsv, Usage(input_tokens=2000, output_tokens=100, tool_calls=2))
    assert recorded.cost_micros == 200 + 50 + 20_000


def test_partial_usage_is_committed_and_the_rest_released(make_guard: GuardFactory) -> None:
    """A failed call that reported only input tokens still pays for them; the reservation closes."""
    guard = make_guard()
    rsv = guard.reserve(["session:s"], 200_000, model=MODEL)
    guard.commit(rsv, Usage(input_tokens=5000))  # partial: no output reported
    status = guard.status()
    assert status.spent_micros == 500
    assert status.reserved_micros == 0


def test_commit_is_idempotent_and_release_after_commit_is_a_no_op(
    make_guard: GuardFactory,
) -> None:
    ledger = MemoryLedger()
    guard = make_guard(ledger=ledger)
    rsv = guard.reserve(["session:s"], 10_000, model=MODEL)
    guard.commit(rsv, Usage(input_tokens=1000))
    again = guard.commit(rsv, Usage(input_tokens=1000))
    guard.release(rsv)
    assert again.cost_micros == 0
    assert guard.status().spent_micros == 100
    assert [e.event for e in ledger.events()] == ["reserve", "commit"]


def test_commit_after_release_does_not_spend(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:s"], 10_000, model=MODEL)
    guard.release(rsv)
    guard.commit(rsv, Usage(input_tokens=1_000_000))
    assert guard.status().spent_micros == 0


def test_overrun_is_recorded_in_full_and_logged(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:s"], 100, model=MODEL)
    with capture_logs() as logs:
        guard.commit(rsv, Usage(input_tokens=10_000))  # costs 1000 > reserved 100
    assert guard.status().spent_micros == 1000
    assert any(entry["event"] == "budget_reservation_overrun" for entry in logs)


def test_non_billing_guard_commits_zero_but_still_enforces_caps(make_guard: GuardFactory) -> None:
    guard = make_guard("0.001", bills=False)  # cap 1000 micros, effective 950
    rsv = guard.reserve(["adhoc:a"], 900, model=MODEL, mode="mock")
    recorded = guard.commit(rsv, Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert recorded.cost_micros == 0
    assert guard.status().spent_micros == 0
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:a"], 951, model=MODEL, mode="mock")


def test_commit_cost_rejects_negative(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["adhoc:a"], 10, model=MODEL)
    with pytest.raises(ValueError):
        guard.commit_cost(rsv, -1)


# ---------------------------------------------------------------------------- unknown model
def test_unknown_model_price_refuses_estimation(make_guard: GuardFactory) -> None:
    guard = make_guard()
    with pytest.raises(UnknownModelPrice):
        guard.estimate_worst_case("gpt-9-nova", prompt_chars=100, max_output_tokens=10)


def test_estimate_worst_case_uses_the_conservative_estimator(make_guard: GuardFactory) -> None:
    guard = make_guard()
    # 3000 chars / 3.0 + 64 overhead = 1064 tokens x 0.10 = 107 (rounded up); 1000 out x 0.50 = 500
    assert guard.estimate_worst_case(MODEL, prompt_chars=3000, max_output_tokens=1000) == 607


# ---------------------------------------------------------------------------- tripwires
def test_tripwire_levels(make_guard: GuardFactory) -> None:
    cfg = make_guard().config
    assert cfg.tripwire(0) == "ok"
    assert cfg.tripwire(2_999_999) == "ok"
    assert cfg.tripwire(3_000_000) == "warn_50"
    assert cfg.tripwire(4_799_999) == "warn_50"
    assert cfg.tripwire(4_800_000) == "must_run_only_80"
    assert cfg.tripwire(5_699_999) == "must_run_only_80"
    assert cfg.tripwire(5_700_000) == "refuse_95"


def test_50_percent_logs_a_warning_once(make_guard: GuardFactory) -> None:
    guard = make_guard()
    _spend(guard, 3_000_000)
    with capture_logs() as logs:
        guard.reserve(["adhoc:a"], 10, purpose="normal", model=MODEL)
        guard.reserve(["adhoc:a"], 10, purpose="normal", model=MODEL)
    warnings = [e for e in logs if e["event"] == "budget_tripwire_50"]
    assert len(warnings) == 1
    assert guard.status().tripwire == "warn_50"


def test_80_percent_allows_only_must_run_calls(make_guard: GuardFactory) -> None:
    guard = make_guard()
    _spend(guard, 4_800_000)
    assert guard.status().tripwire == "must_run_only_80"
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(["adhoc:a"], 10, purpose="eval:L1", model=MODEL)
    assert exc.value.details["tripwire"] == "must_run_only_80"
    guard.reserve(["adhoc:a"], 10, purpose="must-run:l0-smoke", model=MODEL)


def test_95_percent_refuses_everything_even_must_run(make_guard: GuardFactory) -> None:
    guard = make_guard()
    _spend(guard, 5_700_000)
    assert guard.status().tripwire == "refuse_95"
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(["adhoc:a"], 1, purpose=MUST, model=MODEL)
    assert exc.value.details["tripwire"] == "refuse_95"


def test_open_reservations_trip_the_wire_too(make_guard: GuardFactory) -> None:
    guard = make_guard()
    guard.reserve(["adhoc:a"], 4_800_000, purpose=MUST, model=MODEL)
    with pytest.raises(BudgetExceeded):
        guard.reserve(["adhoc:b"], 10, purpose="normal", model=MODEL)


def test_status_reports_cap_and_scopes(make_guard: GuardFactory) -> None:
    guard = make_guard()
    rsv = guard.reserve(["session:s", "research:r"], 1000, model=MODEL, mode="live")
    guard.commit_cost(rsv, 700)
    status = guard.status()
    assert status.cap_micros == 6_000_000
    assert status.effective_cap_micros == 5_700_000
    assert status.spent_micros == 700
    assert status.by_scope["research:r"] == 700
    scope = guard.usage_for_scope("research:r")
    assert (scope.spent_micros, scope.cap_micros) == (700, usd("0.30"))
