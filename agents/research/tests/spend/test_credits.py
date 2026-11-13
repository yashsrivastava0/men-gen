"""The global provider-credit ledger: baseline, counting, the newest anchor wins, atomic and lock-safe writes, never
reset by a budget restart, and the key itself never written."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mde_core.clock import FakeClock
from mde_research_agent.spend import BudgetControl, SpendRecord
from mde_research_agent.spend.credits import (
    FILE_NAME,
    CreditLedger,
    current_run_id,
    derive_view,
    describe,
    key_fingerprint,
    level_of,
    per_run,
    set_current_run,
)

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def make(tmp_path: Path, clock: FakeClock | None = None) -> tuple[CreditLedger, FakeClock, Path]:
    clock = clock or FakeClock(T0)
    path = tmp_path / FILE_NAME
    return CreditLedger(path, clock=clock), clock, path


def view(ledger: CreditLedger, provider: str) -> dict[str, Any]:
    return ledger.snapshot()["providers"][provider]["view"]


# ------------------------------------------------------------------------------------------------ baseline
def test_an_empty_ledger_knows_nothing_and_says_so(tmp_path: Path) -> None:
    ledger, _, path = make(tmp_path)
    v = view(ledger, "tavily")
    assert v["used"] is None and v["remaining"] is None and v["source"] == "counted"
    assert level_of(v) == "unknown"
    assert not path.exists()  # nothing to write yet


def test_a_used_baseline_gives_used_limit_and_remaining(tmp_path: Path) -> None:
    ledger, clock, path = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    v = view(ledger, "tavily")
    assert (v["used"], v["limit"], v["remaining"], v["source"]) == (93, 1000, 907, "counted")
    assert v["as_of"] == clock.now().isoformat()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == "research.credits.v1"
    assert on_disk["providers"]["tavily"]["baseline"] == {
        "kind": "used",
        "amount": 93,
        "limit": 1000,
        "as_of": clock.now().isoformat(),
    }


def test_a_remaining_baseline_counts_down(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.set_baseline("serper", "remaining", 2389)
    v = view(ledger, "serper")
    assert (v["used"], v["limit"], v["remaining"]) == (None, None, 2389)
    ledger.add_credits("serper", 11, "rrn_1")
    assert view(ledger, "serper")["remaining"] == 2378
    # with a known allowance the used figure follows too
    ledger.set_baseline("serper", "remaining", 2389, 2500)
    v = view(ledger, "serper")
    assert (v["used"], v["limit"], v["remaining"]) == (111, 2500, 2389)


def test_counting_adds_to_the_baseline_and_a_new_baseline_restarts_the_count(
    tmp_path: Path,
) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    ledger.add_credits("tavily", 32, "rrn_1")
    ledger.add_credits("tavily", 2, "rrn_1")
    assert view(ledger, "tavily")["used"] == 127
    ledger.set_baseline("tavily", "used", 130, 1000)
    assert view(ledger, "tavily")["used"] == 130  # counting starts again from the new figure


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("tavily", "balance", 1), "kind"),
        (("nobody", "used", 1), "provider"),
        (("tavily", "used", -1), "whole number"),
        (("tavily", "used", True), "whole number"),
        (("tavily", "used", 1.5), "whole number"),
        (("tavily", "used", 5, 3), "more than the limit"),
        (("tavily", "used", 5, -1), "whole number"),
    ],
)
def test_impossible_baselines_are_refused(
    tmp_path: Path, args: tuple[Any, ...], message: str
) -> None:
    ledger, _, path = make(tmp_path)
    with pytest.raises(ValueError, match=message):
        ledger.set_baseline(*args)
    assert not path.exists()


# ------------------------------------------------------------------------------------------------ the anchors
def test_the_providers_own_reading_wins_and_is_extended_by_what_is_counted_after_it(
    tmp_path: Path,
) -> None:
    ledger, clock, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    clock.advance(60)
    read_at = clock.now()
    ledger.record_reading(
        "tavily",
        {"used": 96, "limit": 1000, "plan": "Researcher", "read_at": read_at, "scope": "account"},
    )
    v = view(ledger, "tavily")
    assert (v["used"], v["limit"], v["remaining"], v["source"], v["plan"]) == (
        96,
        1000,
        904,
        "provider",
        "Researcher",
    )
    clock.advance(5)
    ledger.add_credits("tavily", 4, "rrn_2")
    v = view(
        ledger, "tavily"
    )  # the reading plus what was counted since it: the app's own count again
    assert (v["used"], v["source"], v["read_at"]) == (100, "counted", read_at.isoformat())
    clock.advance(5)
    ledger.record_reading("tavily", {"used": 101, "limit": 1000, "read_at": clock.now()})
    assert (view(ledger, "tavily")["used"], view(ledger, "tavily")["source"]) == (101, "provider")


def test_a_baseline_newer_than_the_reading_wins(tmp_path: Path) -> None:
    ledger, clock, _ = make(tmp_path)
    ledger.record_reading("tavily", {"used": 96, "limit": 1000, "read_at": clock.now()})
    clock.advance(30)
    ledger.set_baseline("tavily", "used", 200, 1000)  # the owner typed a newer figure
    assert view(ledger, "tavily")["used"] == 200 and view(ledger, "tavily")["source"] == "counted"


def test_a_reading_without_a_limit_borrows_the_baselines_limit(tmp_path: Path) -> None:
    ledger, clock, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    clock.advance(1)
    ledger.record_reading("tavily", {"used": 95, "limit": None, "read_at": clock.now()})
    v = view(ledger, "tavily")
    assert (v["used"], v["limit"], v["remaining"]) == (95, 1000, 905)


def test_credits_with_no_baseline_are_just_counted(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.add_credits("serper", 3)
    v = view(ledger, "serper")
    assert (v["used"], v["limit"], v["remaining"], v["source"]) == (3, None, None, "counted")


def test_reading_validation(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    for bad in (
        {"used": None},
        {"used": -3},
        {"used": 4, "limit": "x"},
        {"used": 1, "source": "guess"},
    ):
        with pytest.raises(ValueError):
            ledger.record_reading("tavily", bad)
    ledger.record_reading("tavily", {"used": 5, "source": "unverified_endpoint"})
    assert view(ledger, "tavily")["source"] == "unverified_endpoint"


# ------------------------------------------------------------------------------------------------ per run
def test_runs_are_totalled_per_provider_and_bounded_to_the_last_fifty(tmp_path: Path) -> None:
    ledger, clock, _ = make(tmp_path)
    ledger.add_credits("tavily", 3, "rrn_a")
    ledger.add_credits("tavily", 2, "rrn_a")
    ledger.add_credits("serper", 4, "rrn_a")
    ledger.add_credits("tavily", 7)  # a ping: counted, but in no run
    assert ledger.run_credits("rrn_a") == {"tavily": 5, "serper": 4}
    assert ledger.run_credits("nobody") == {}
    snap = ledger.snapshot()
    assert snap["providers"]["tavily"]["counted_outside_runs"] == 7
    assert snap["providers"]["tavily"]["counted_since_baseline"] == 12
    for i in range(60):
        clock.advance(1)
        ledger.add_credits("tavily", 1, f"rrn_{i:02d}")
    runs = ledger.snapshot()["providers"]["tavily"]["runs"]
    assert len(runs) == 50 and runs[-1]["run_id"] == "rrn_59" and runs[0]["run_id"] == "rrn_10"
    table = per_run(ledger.snapshot())
    assert table[0]["run_id"] == "rrn_59" and table[0]["credits"] == {"tavily": 1}
    assert len(table) == 50


def test_zero_credits_write_nothing(tmp_path: Path) -> None:
    ledger, _, path = make(tmp_path)
    ledger.add_credits("tavily", 0, "rrn_1")
    assert not path.exists()


def test_the_run_context_is_per_task() -> None:
    import asyncio

    async def inside(rid: str) -> str | None:
        set_current_run(rid)
        await asyncio.sleep(0)
        return current_run_id()

    async def main() -> list[str | None]:
        a, b = await asyncio.gather(
            asyncio.create_task(inside("rrn_a")), asyncio.create_task(inside("rrn_b"))
        )
        return [a, b, current_run_id()]

    assert asyncio.run(main()) == ["rrn_a", "rrn_b", None]


# ------------------------------------------------------------------------------------------------ the file
def test_the_key_is_never_stored_only_its_fingerprint(tmp_path: Path) -> None:
    secret = "tvly-this-must-never-reach-the-disk-0001"  # secret-scan: allow
    fp = key_fingerprint(secret)
    assert len(fp) == 12 and secret not in fp
    ledger, _, path = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000, fingerprint=fp)
    ledger.add_credits("tavily", 1, "rrn_1", fingerprint=fp)
    text = path.read_text(encoding="utf-8")
    assert secret not in text and fp in text
    assert ledger.snapshot()["providers"]["tavily"]["key_fingerprint"] == fp


def test_a_different_key_is_a_different_account(tmp_path: Path) -> None:
    ledger, clock, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000, fingerprint="aaaaaaaaaaaa")
    ledger.add_credits("tavily", 5, "rrn_1", fingerprint="aaaaaaaaaaaa")
    clock.advance(10)
    ledger.add_credits("tavily", 2, "rrn_2", fingerprint="bbbbbbbbbbbb")
    rec = ledger.snapshot()["providers"]["tavily"]
    assert rec["key_fingerprint"] == "bbbbbbbbbbbb" and rec["key_changed_at"]
    assert rec["baseline"] is None and rec["view"]["used"] == 2  # the old balance does not apply
    assert rec["previous_keys"][0]["key_fingerprint"] == "aaaaaaaaaaaa"
    assert rec["previous_keys"][0]["baseline"]["amount"] == 93
    note = describe(ledger.snapshot())[0]["note"]
    assert "different Tavily key" in note


def test_a_baseline_entered_before_any_key_was_seen_binds_to_the_first_key(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.set_baseline("serper", "remaining", 2389)  # the command line knows no key
    ledger.add_credits("serper", 1, "rrn_1", fingerprint="cccccccccccc")
    rec = ledger.snapshot()["providers"]["serper"]
    assert rec["key_fingerprint"] == "cccccccccccc" and rec["view"]["remaining"] == 2388


def test_the_write_is_atomic_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, _, path = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    before = path.read_bytes()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
    real = os.replace

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    ledger.add_credits(
        "tavily", 5, "rrn_1"
    )  # the write fails: no exception, the file is still the old one
    assert ledger.write_failed is True and path.read_bytes() == before
    monkeypatch.setattr(os, "replace", real)
    ledger.add_credits("tavily", 1, "rrn_1")
    assert ledger.write_failed is False
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["providers"]["tavily"]["counted_since_baseline"] >= 1


def test_a_damaged_file_is_set_aside_not_lost(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    path.write_text("{not json", encoding="utf-8")
    ledger = CreditLedger(path)
    assert view(ledger, "tavily")["used"] is None
    assert (tmp_path / (FILE_NAME + ".corrupt")).read_text(encoding="utf-8") == "{not json"
    ledger.set_baseline("tavily", "used", 1, 10)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "research.credits.v1"


def test_a_second_writer_on_the_same_file_sees_the_first_ones_figures(tmp_path: Path) -> None:
    a, _, path = make(tmp_path)
    b = CreditLedger(path)
    a.set_baseline("tavily", "used", 93, 1000)
    b.add_credits("tavily", 7, "rrn_1")
    a.add_credits("tavily", 1, "rrn_1")
    assert view(b, "tavily")["used"] == 101 and view(a, "tavily")["used"] == 101


def test_concurrent_adds_from_threads_lose_nothing(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 0, 1000)

    def work(i: int) -> None:
        for _ in range(40):
            ledger.add_credits("tavily", 1, f"rrn_{i}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert view(ledger, "tavily")["used"] == 320
    assert sum(r["credits"] for r in ledger.snapshot()["providers"]["tavily"]["runs"]) == 320


@pytest.mark.skipif(sys.platform == "win32", reason="the cross-process lock is POSIX (fcntl)")
def test_two_processes_writing_at_once_lose_nothing(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    CreditLedger(path).set_baseline("serper", "remaining", 1000)
    code = (
        "import sys\n"
        "from mde_research_agent.spend.credits import CreditLedger\n"
        "l = CreditLedger(sys.argv[1])\n"
        "for _ in range(25):\n"
        "    l.add_credits('serper', 1, sys.argv[2])\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(path), f"rrn_{i}"]) for i in range(3)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]
    assert view(CreditLedger(path), "serper")["remaining"] == 1000 - 75


# ------------------------------------------------------------------------------------------------ outside the periods
def test_a_budget_restart_never_touches_the_credit_ledger(tmp_path: Path) -> None:
    ledger, clock, path = make(tmp_path)
    ledger.set_baseline("tavily", "used", 93, 1000)
    ledger.add_credits("tavily", 33, "rrn_1")
    before = path.read_bytes()
    record = SpendRecord(
        tmp_path / "spend-record.json",
        ledger_for=lambda pid: f"data/.budget/research/ledger-{pid}.jsonl",
        clock=clock,
    )
    control = BudgetControl(
        record, total_micros=5_000_000, tripwire_micros=4_500_000, repo_root=tmp_path
    )
    control.restart()
    control.restart()
    assert len(record.data["periods"]) == 3
    assert path.read_bytes() == before
    assert view(CreditLedger(path), "tavily")["used"] == 126
    assert "credit" not in (tmp_path / "spend-record.json").read_text(encoding="utf-8").lower()


# ------------------------------------------------------------------------------------------------ levels and words
@pytest.mark.parametrize(
    ("view_", "need", "level"),
    [
        ({"used": 93, "limit": 1000, "remaining": 907}, 41, "ok"),
        ({"used": 800, "limit": 1000, "remaining": 200}, 41, "amber"),
        ({"used": 949, "limit": 1000, "remaining": 51}, None, "amber"),
        ({"used": 950, "limit": 1000, "remaining": 50}, None, "red"),
        (
            {"used": 970, "limit": 1000, "remaining": 30},
            41,
            "red",
        ),  # less than one run's high estimate
        ({"used": None, "limit": None, "remaining": 2389}, 41, "ok"),
        ({"used": None, "limit": None, "remaining": 100}, 41, "amber"),  # fewer than three runs
        ({"used": None, "limit": None, "remaining": 40}, 41, "red"),
        ({"used": None, "limit": None, "remaining": 0}, None, "red"),
        ({"used": 1200, "limit": 1000, "remaining": 0}, None, "red"),
        ({"used": None, "limit": None, "remaining": None}, 41, "unknown"),
    ],
)
def test_levels(view_: dict[str, Any], need: int | None, level: str) -> None:
    assert level_of(view_, need) == level


def test_describe_gives_the_console_everything_in_plain_words(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.set_baseline("tavily", "used", 970, 1000)
    ledger.set_baseline("serper", "remaining", 2389)
    rows = describe(
        ledger.snapshot(),
        need_high={"tavily": 41, "serper": 41},
        keys={"tavily": True, "serper": False},
    )
    tavily, serper = rows
    assert tavily["provider"] == "tavily" and tavily["label"] == "Tavily"
    assert tavily["unit"] == "credit"
    assert tavily["level"] == "red" and "may stop early" in tavily["note"]
    assert tavily["can_check"] is True
    assert serper["level"] == "ok" and serper["key_present"] is False
    assert serper["can_check"] is False and "does not report its balance" in serper["note"]
    assert set(tavily) >= {
        "used",
        "limit",
        "remaining",
        "source",
        "as_of",
        "read_at",
        "plan",
        "note",
        "baseline",
    }


def test_derive_view_is_pure_and_tolerates_a_bare_record() -> None:
    assert derive_view({})["used"] is None
    assert derive_view({"counted_since_baseline": 4})["used"] == 4


def test_timestamps_are_utc(tmp_path: Path) -> None:
    ledger, _, _ = make(tmp_path)
    ledger.add_credits("tavily", 1, "rrn_1")
    rec = ledger.snapshot()["providers"]["tavily"]
    for stamp in (rec["last_counted_at"], rec["runs"][0]["at"]):
        assert datetime.fromisoformat(stamp).utcoffset() == timedelta(0)
