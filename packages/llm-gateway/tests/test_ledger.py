"""Ledger: format, re-derivation on restart, stale reservations, corruption, cross-process safety."""

from __future__ import annotations

import json
import multiprocessing
from datetime import timedelta
from pathlib import Path

import pytest

from mde_contracts.gateway import Usage
from mde_core.clock import FakeClock
from mde_core.errors import BudgetExceeded
from mde_llm_gateway.budget import BudgetGuard
from mde_llm_gateway.ledger import FileLedger, LedgerEvent
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.testing import (
    GuardFactory,
    budget_config_for_tests,
    reserve_commit_worker,
    reserve_hold_worker,
)

MODEL = "gpt-6-luna"


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_ledger_lines_have_the_documented_shape(make_guard: GuardFactory, tmp_path: Path) -> None:
    guard = make_guard(ledger="file")
    rsv = guard.reserve(
        ["session:s", "research:r"],
        5000,
        call_id="call_1",
        spec_id="C1.x",
        model=MODEL,
        mode="live",
    )
    guard.commit(rsv, Usage(input_tokens=1000, cached_input_tokens=200, output_tokens=300))
    other = guard.reserve(["session:s"], 100, call_id="call_2", spec_id="C1.x", model=MODEL)
    guard.release(other)
    lines = _lines(tmp_path / "ledger.jsonl")
    assert [x["event"] for x in lines] == ["reserve", "commit", "reserve", "release"]
    reserve, commit = lines[0], lines[1]
    assert reserve["scopes"] == ["session:s", "research:r"]
    assert reserve["worst_case_micros"] == 5000
    assert reserve["call_id"] == "call_1" and reserve["spec_id"] == "C1.x"
    assert reserve["model"] == MODEL and reserve["mode"] == "live"
    assert commit["reservation_id"] == reserve["reservation_id"]
    assert commit["cost_micros"] == 80 + 2 + 150
    usage = commit["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 1000 and usage["cached_input_tokens"] == 200
    assert "ts" in reserve and str(reserve["ts"]).endswith("Z")


def test_totals_are_re_derived_from_the_file_on_start(
    make_guard: GuardFactory, tmp_path: Path, clock: FakeClock
) -> None:
    guard = make_guard(ledger="file")
    rsv = guard.reserve(["session:s"], 5000, model=MODEL, mode="live")
    guard.commit_cost(rsv, 1234)
    guard.reserve(["session:s"], 777, model=MODEL)  # left open

    fresh = BudgetGuard(
        budget_config_for_tests("6.00"),
        Pricing.load(),
        FileLedger(tmp_path / "ledger.jsonl", clock=clock),
        bills=True,
    )
    status = fresh.status()
    assert status.spent_micros == 1234
    assert status.reserved_micros == 777
    assert status.by_scope == {"session:s": 1234}
    assert status.live_calls == 1


def test_two_guards_on_one_file_share_one_truth(
    make_guard: GuardFactory, tmp_path: Path, clock: FakeClock
) -> None:
    a = make_guard(ledger="file")
    b = BudgetGuard(
        budget_config_for_tests("6.00"),
        Pricing.load(),
        FileLedger(tmp_path / "ledger.jsonl", clock=clock),
        bills=True,
    )
    held = a.reserve(["adhoc:x"], 5_000_000, purpose="must-run", model=MODEL)
    with pytest.raises(BudgetExceeded):  # b sees a's reservation
        b.reserve(["adhoc:x"], 1_000_000, purpose="must-run", model=MODEL)
    a.commit_cost(held, 5_000_000)
    assert b.status().spent_micros == 5_000_000


def test_stale_unclosed_reservations_count_as_spent_not_released(
    make_guard: GuardFactory, clock: FakeClock
) -> None:
    """A reservation nobody closed (crashed process, failed commit) may have been billed: after the
    ttl its worst case counts as SPENT. The budget is never freed by the passage of time."""
    guard = make_guard(ledger="file")
    guard.reserve(["adhoc:x"], 5_000_000, purpose="must-run", model=MODEL, mode="live")
    status = guard.status()
    assert (status.spent_micros, status.reserved_micros) == (0, 5_000_000)
    clock.advance(600)  # exactly the ttl: still held
    status = guard.status()
    assert (status.spent_micros, status.reserved_micros) == (0, 5_000_000)
    clock.advance(1)  # older than 10 minutes: burnt
    status = guard.status()
    assert (status.spent_micros, status.reserved_micros) == (5_000_000, 0)
    assert status.by_scope == {"adhoc:x": 5_000_000}
    with pytest.raises(BudgetExceeded):  # the budget did NOT come back
        guard.reserve(["adhoc:x"], 5_000_000, purpose="must-run", model=MODEL)
    snap = guard.ledger.snapshot()
    assert (snap.stale_reservations, snap.stale_micros) == (1, 5_000_000)


def test_a_late_commit_for_an_expired_reservation_replaces_the_worst_case(
    make_guard: GuardFactory, clock: FakeClock
) -> None:
    guard = make_guard(ledger="file")
    rsv = guard.reserve(["adhoc:x"], 1000, model=MODEL)
    clock.advance(3600)
    assert guard.status().spent_micros == 1000  # stale: counted at its worst case
    guard.commit_cost(rsv, 900)
    assert guard.status().spent_micros == 900  # the real cost replaces it, counted once


def test_corrupt_lines_are_skipped_and_counted(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = FileLedger(path, clock=clock)
    guard = BudgetGuard(budget_config_for_tests("6.00"), Pricing.load(), ledger, bills=True)
    rsv = guard.reserve(["adhoc:x"], 1000, model=MODEL, mode="live")
    guard.commit_cost(rsv, 500)
    with open(path, "ab") as fh:
        fh.write(b"this is not json\n")
        fh.write(b'{"event":"commit","ts":"2026-11-08T12:00:00Z","reservation_id":"x"}\n')
    fresh = FileLedger(path, clock=clock)
    snap = fresh.peek()
    assert snap.spent_micros == 500
    assert snap.corrupt_lines == 2


def test_a_partial_last_line_is_ignored_then_repaired_by_the_next_append(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "ledger.jsonl"
    guard = BudgetGuard(
        budget_config_for_tests("6.00"),
        Pricing.load(),
        FileLedger(path, clock=clock),
        bills=True,
    )
    rsv = guard.reserve(["adhoc:x"], 1000, model=MODEL, mode="live")
    guard.commit_cost(rsv, 500)
    with open(path, "ab") as fh:
        fh.write(b'{"event":"reserve","ts":"2026-11-08T12:00:00Z","reserv')  # crashed writer
    assert FileLedger(path, clock=clock).peek().spent_micros == 500  # partial tail ignored
    rsv2 = guard.reserve(["adhoc:x"], 1000, model=MODEL, mode="live")
    guard.commit_cost(rsv2, 250)
    snap = FileLedger(path, clock=clock).peek()
    assert snap.spent_micros == 750
    assert snap.corrupt_lines == 1  # the garbage became its own (skipped) line


def test_a_truncated_ledger_is_re_derived(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = FileLedger(path, clock=clock)
    guard = BudgetGuard(budget_config_for_tests("6.00"), Pricing.load(), ledger, bills=True)
    rsv = guard.reserve(["adhoc:x"], 1000, model=MODEL)
    guard.commit_cost(rsv, 500)
    assert ledger.peek().spent_micros == 500
    path.write_text("", encoding="utf-8")
    assert ledger.peek().spent_micros == 0


def test_peek_does_not_create_files(tmp_path: Path) -> None:
    path = tmp_path / "nope" / "ledger.jsonl"
    snap = FileLedger(path).peek()
    assert snap.spent_micros == 0
    assert not path.parent.exists()


def test_ledger_event_format_is_closed() -> None:
    with pytest.raises(ValueError):
        LedgerEvent.model_validate_json(
            '{"event":"reserve","ts":"2026-11-08T12:00:00Z","reservation_id":"r","extra":1}'
        )
    with pytest.raises(ValueError):  # naive timestamps are rejected (UTC only)
        LedgerEvent.model_validate_json(
            '{"event":"release","ts":"2026-11-08T12:00:00","reservation_id":"r"}'
        )
    ledger = FileLedger(Path("unused.jsonl"))
    with pytest.raises(ValueError):
        ledger.reserve(
            LedgerEvent(event="release", ts=FakeClock().now(), reservation_id="r"), lambda _s: None
        )
    with pytest.raises(ValueError):
        ledger.append(
            LedgerEvent(
                event="reserve",
                ts=FakeClock().now(),
                reservation_id="r",
                scopes=["a"],
                worst_case_micros=1,
            )
        )


def test_stale_window_is_configurable(tmp_path: Path, clock: FakeClock) -> None:
    ledger = FileLedger(tmp_path / "l.jsonl", clock=clock, reservation_ttl=5)
    guard = BudgetGuard(budget_config_for_tests("6.00"), Pricing.load(), ledger, bills=True)
    guard.reserve(["adhoc:x"], 1000, model=MODEL)
    clock.advance(6)
    status = guard.status()
    assert (status.reserved_micros, status.spent_micros) == (0, 1000)
    assert timedelta(seconds=5) == ledger.reservation_ttl


# ---------------------------------------------------------------------------- multi-process
def _run_workers(
    worker: object, path: Path, cap: int, procs: int, attempts: int, worst: int
) -> int:
    ctx = multiprocessing.get_context("spawn")
    args = [(str(path), cap, attempts, worst)] * procs
    with ctx.Pool(procs) as pool:
        return sum(pool.starmap(worker, args))  # type: ignore[arg-type]


def test_concurrent_processes_never_exceed_the_cap_with_commits(tmp_path: Path) -> None:
    """4 processes x 20 attempts race for room for exactly 20 reservations (cap 20_000, 1_000 each)."""
    path = tmp_path / "ledger.jsonl"
    granted = _run_workers(reserve_commit_worker, path, 20_000, 4, 20, 1_000)
    assert granted == 20  # not 19 (lost update), not 21 (race past the cap)
    snap = FileLedger(path).peek()
    assert snap.spent_micros == 20_000
    assert snap.reserved_micros == 0
    assert snap.corrupt_lines == 0
    assert snap.lines == 40  # 20 reserve + 20 commit lines, every one intact


def test_concurrent_processes_never_exceed_the_cap_with_open_reservations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    granted = _run_workers(reserve_hold_worker, path, 15_000, 3, 15, 1_000)
    assert granted == 15
    snap = FileLedger(path).peek()
    assert snap.reserved_micros == 15_000
    assert snap.corrupt_lines == 0
