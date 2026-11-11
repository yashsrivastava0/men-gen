"""The ledger guard file (review item M4): a deleted or truncated ledger must not hand out a fresh
budget; the operator resets on purpose (``init --force`` or ``MDE_BUDGET_LEDGER_RESET=1``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mde_core.clock import FakeClock
from mde_core.errors import BudgetExceeded, LedgerIntegrityError
from mde_core.settings import Settings
from mde_llm_gateway import budget_cli
from mde_llm_gateway.budget import BudgetGuard
from mde_llm_gateway.errors import ERROR_CLASSES, ERROR_STATUS
from mde_llm_gateway.factory import create_gateway
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers import ScriptedProvider
from mde_llm_gateway.testing import budget_config_for_tests
from mde_llm_gateway.testing_specs import make_test_registry

MODEL = "gpt-6-luna"


def _guard(path: Path, clock: FakeClock) -> BudgetGuard:
    return BudgetGuard(
        budget_config_for_tests("6.00"), Pricing.load(), FileLedger(path, clock=clock), bills=True
    )


def _spend(guard: BudgetGuard, micros: int = 1_000_000) -> None:
    rsv = guard.reserve(["adhoc:x"], micros, model=MODEL, mode="live", purpose="must-run")
    guard.commit_cost(rsv, micros)


def _guard_file(path: Path) -> dict[str, Any]:
    return json.loads(path.with_name(path.name + ".guard").read_text(encoding="utf-8"))


def test_guard_is_written_at_first_creation_and_only_grows(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "b" / "ledger.jsonl"
    guard = _guard(path, clock)
    assert not path.with_name("ledger.jsonl.guard").exists()  # nothing yet: no ledger either
    rsv = guard.reserve(["adhoc:x"], 500_000, model=MODEL, mode="live")
    first = _guard_file(path)
    assert first["size_bytes"] == path.stat().st_size > 0 and first["spent_micros"] == 0
    guard.commit_cost(rsv, 400_000)
    second = _guard_file(path)
    assert second["size_bytes"] == path.stat().st_size > first["size_bytes"]  # type: ignore[operator]
    assert second["spent_micros"] == 400_000
    assert second["created_at"] == first["created_at"]


def test_deleted_ledger_is_detected_and_refuses_new_reservations(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    _spend(guard, 4_000_000)
    path.unlink()  # the "I will just delete it and get a fresh USD 6" move
    with pytest.raises(LedgerIntegrityError) as exc:
        guard.reserve(["adhoc:x"], 1_000, model=MODEL, mode="live", purpose="must-run")
    assert "missing" in exc.value.message and "init --force" in exc.value.message
    assert isinstance(exc.value, BudgetExceeded)  # callers that handle budget refusals handle it
    assert not path.exists()  # and nothing was recreated behind the operator's back
    # a fresh process sees the same thing
    with pytest.raises(LedgerIntegrityError):
        _guard(path, clock).reserve(
            ["adhoc:x"], 1_000, model=MODEL, mode="live", purpose="must-run"
        )


def test_truncated_and_edited_ledgers_are_detected(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    _spend(guard, 2_000_000)
    _spend(guard, 1_000_000)
    original = path.read_bytes()

    path.write_bytes(b"")  # emptied
    with pytest.raises(LedgerIntegrityError, match="truncated or replaced"):
        guard.reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")

    path.write_bytes(original[: len(original) // 2])  # cut in half
    with pytest.raises(LedgerIntegrityError, match="truncated or replaced"):
        _guard(path, clock).reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")

    # same size, spend rewritten to a smaller number: the recorded spend gives it away
    forged = original.replace(b'"cost_micros":2000000', b'"cost_micros":0000001')
    assert len(forged) == len(original) and forged != original
    path.write_bytes(forged)
    with pytest.raises(LedgerIntegrityError, match="lines were removed"):
        _guard(path, clock).reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")

    path.write_bytes(original)  # restored: everything works again
    _guard(path, clock).reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")


def test_commits_are_still_recorded_when_the_guard_is_unhappy(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Only a reserve is refused: money that was already spent must always be written down."""
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    rsv = guard.reserve(["adhoc:x"], 5_000, model=MODEL, mode="live")
    before = _guard_file(path)["size_bytes"]
    path.unlink()
    guard.commit_cost(rsv, 4_000)  # recreates a ledger that is even BIGGER than the guard remembers
    assert FileLedger(path).peek().spent_micros == 4_000
    assert _guard_file(path)["size_bytes"] >= before  # the guard never shrinks to match
    # ... yet it is a different file (other first line, fewer lines): still refused
    with pytest.raises(LedgerIntegrityError, match="replaced"):
        guard.reserve(["adhoc:x"], 10, model=MODEL, mode="live")


def test_a_ledger_from_before_the_guard_existed_is_adopted(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    _spend(guard, 1_000_000)
    path.with_name("ledger.jsonl.guard").unlink()  # an old ledger without a guard
    _guard(path, clock).reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")
    assert _guard_file(path)["spent_micros"] == 1_000_000


def test_a_corrupt_guard_refuses_rather_than_trusts(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    _spend(guard)
    path.with_name("ledger.jsonl.guard").write_text("not json", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="corrupt"):
        guard.reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")


def test_the_error_is_mapped_for_the_http_api() -> None:
    assert ERROR_CLASSES["ledger_integrity"] is LedgerIntegrityError
    assert ERROR_STATUS["ledger_integrity"] == 503


# ---------------------------------------------------------------------------- factory: refuse to start
def _live_settings(ledger: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        llm_mode="live",
        forbid_live=False,
        openai_api_key="sk-" + "test" * 12,
        llm_budget_ledger=ledger,
    )


def _make(ledger: Path):  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(mode="live", billable=True)
    return create_gateway(_live_settings(ledger), make_test_registry(), provider=provider)


def test_a_live_gateway_refuses_to_start_on_a_deleted_ledger(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDE_BUDGET_LEDGER_RESET", raising=False)
    path = tmp_path / "ledger.jsonl"
    _spend(_guard(path, clock), 3_000_000)
    assert _make(path).budget_status().spent_micros == 3_000_000  # healthy: starts fine
    path.unlink()
    with pytest.raises(LedgerIntegrityError, match="missing"):
        _make(path)
    path.write_text("", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="truncated"):
        _make(path)


def test_explicit_reset_by_environment_variable(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.jsonl"
    _spend(_guard(path, clock), 3_000_000)
    path.unlink()
    monkeypatch.setenv("MDE_BUDGET_LEDGER_RESET", "1")
    gateway = _make(path)  # the operator said so: accepted
    assert gateway.budget_status().spent_micros == 0
    monkeypatch.delenv("MDE_BUDGET_LEDGER_RESET")
    _make(path)  # and it stays accepted afterwards: the guard was re-baselined
    gateway.guard.reserve(["session:x"], 10, model=MODEL, mode="live", purpose="must-run")


def test_mock_and_replay_gateways_never_touch_the_ledger_machinery(tmp_path: Path) -> None:
    path = tmp_path / "never" / "ledger.jsonl"
    settings = Settings(_env_file=None, llm_mode="mock", llm_budget_ledger=path)  # type: ignore[call-arg]
    gateway = create_gateway(settings, make_test_registry())
    assert not gateway.guard.bills
    assert not path.parent.exists()


# ---------------------------------------------------------------------------- the CLI
def test_cli_init_creates_ledger_and_guard_and_refuses_after_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], clock: FakeClock
) -> None:
    path = tmp_path / "fresh" / "ledger.jsonl"
    assert budget_cli.main(["init", "--ledger", str(path)]) == 0
    assert path.exists() and path.stat().st_size == 0
    assert _guard_file(path)["size_bytes"] == 0
    assert "initialised" in capsys.readouterr().out
    assert budget_cli.main(["init", "--ledger", str(path)]) == 0  # idempotent
    assert "already initialised" in capsys.readouterr().out

    _spend(_guard(path, clock), 2_000_000)
    path.unlink()
    assert budget_cli.main(["init", "--ledger", str(path)]) == 2  # refuses without --force
    assert "refused" in capsys.readouterr().err
    assert not path.exists()
    assert budget_cli.main(["init", "--force", "--ledger", str(path)]) == 0  # explicit reset
    assert path.exists() and "guard reset" in capsys.readouterr().out
    _guard(path, clock).reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")


def test_cli_init_adopts_an_unguarded_ledger_and_force_rebaselines_a_truncated_one(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "ledger.jsonl"
    _spend(_guard(path, clock), 1_000_000)
    path.with_name("ledger.jsonl.guard").unlink()
    assert budget_cli.main(["init", "--ledger", str(path)]) == 0
    assert _guard_file(path)["spent_micros"] == 1_000_000
    path.write_text("", encoding="utf-8")
    assert budget_cli.main(["init", "--ledger", str(path)]) == 2
    assert budget_cli.main(["init", "--force", "--ledger", str(path)]) == 0
    assert _guard_file(path)["size_bytes"] == 0


def test_cli_report_warns_about_a_tampered_ledger(
    tmp_path: Path, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.jsonl"
    _spend(_guard(path, clock), 1_000_000)
    path.unlink()
    assert budget_cli.main(["--ledger", str(path)]) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "missing" in out
    assert not path.exists()  # reporting never creates or repairs anything
    assert budget_cli.main(["--json", "--ledger", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["integrity_problem"]


def test_cli_report_shows_stale_reservations(
    tmp_path: Path, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    guard.reserve(["adhoc:x"], 5_000, model=MODEL, mode="live")
    fresh = budget_cli.build_report(FileLedger(path, clock=clock).peek(), guard.config)
    assert fresh["stale_reservations"] == 0 and fresh["open_reservations"] == 1
    clock.advance(601)
    stale = budget_cli.build_report(FileLedger(path, clock=clock).peek(), guard.config)
    assert stale["stale_reservations"] == 1 and stale["stale_usd"] == "0.0050"
    assert stale["spent_micros"] == 5_000 and stale["open_reservations"] == 0
    assert "stale (spent)" in budget_cli.render_text(stale)


def test_a_failing_guard_update_never_fails_the_ledger_write(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line is already in the ledger: a guard that cannot be refreshed only lags (still safe)."""
    path = tmp_path / "ledger.jsonl"
    guard = _guard(path, clock)
    _spend(guard, 1_000_000)
    before = _guard_file(path)

    def broken(*_args: object, **_kw: object) -> None:
        raise OSError("guard volume is read-only")

    with monkeypatch.context() as patched:
        patched.setattr(FileLedger, "_store_guard", broken)
        rsv = guard.reserve(["adhoc:x"], 2_000, model=MODEL, mode="live")  # no exception
        guard.commit_cost(rsv, 1_500)
        assert FileLedger(path).peek().spent_micros == 1_001_500
        assert _guard_file(path) == before  # it lags, it does not lie
    guard.reserve(["adhoc:x"], 10, model=MODEL, mode="live", purpose="must-run")  # still healthy


def test_the_guard_write_retries_when_windows_holds_the_file_open(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("in use by another process")
        real_replace(src, dst)

    monkeypatch.setattr("mde_llm_gateway.ledger.os.replace", flaky)
    monkeypatch.setattr("mde_llm_gateway.ledger.time.sleep", lambda _s: None)
    path = tmp_path / "ledger.jsonl"
    _spend(_guard(path, clock), 1_000)
    assert calls["n"] >= 3 and _guard_file(path)["spent_micros"] == 1_000
