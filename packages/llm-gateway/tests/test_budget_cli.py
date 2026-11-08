from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_core.clock import FakeClock
from mde_llm_gateway import budget_cli
from mde_llm_gateway.testing import GuardFactory


def _seed(make_guard: GuardFactory) -> None:
    guard = make_guard(ledger="file")
    for scope, cost in (
        ("session:ses_1", 120_000),
        ("research:run_1", 80_000),
        ("eval:L1", 1_000_000),
    ):
        rsv = guard.reserve([scope], cost, model="gpt-6-luna", mode="live", purpose="must-run")
        guard.commit_cost(rsv, cost)


def test_text_report(
    make_guard: GuardFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(make_guard)
    assert budget_cli.main(["--ledger", str(tmp_path / "ledger.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "USD 1.2000" in out
    assert "live calls      : 3" in out
    assert "session:ses_1" in out and "cap USD 0.5000" in out
    assert "tripwire        : ok" in out


def test_json_report_and_scope_filter(
    make_guard: GuardFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(make_guard)
    ledger = str(tmp_path / "ledger.jsonl")
    assert budget_cli.main(["--json", "--ledger", ledger, "--scope", "eval"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["spent_micros"] == 1_200_000  # the filter only trims the scope table
    assert report["pct_of_cap"] == "20.0"
    assert list(report["by_scope"]) == ["eval:L1"]
    assert report["by_scope"]["eval:L1"]["cap_usd"] == "1.5000"
    assert report["live_calls"] == 3


def test_missing_ledger_reports_zero_without_creating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "none" / "ledger.jsonl"
    assert budget_cli.main(["--ledger", str(path)]) == 0
    assert "USD 0.0000" in capsys.readouterr().out
    assert not path.parent.exists()


def test_corrupt_lines_are_called_out(
    tmp_path: Path, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text("garbage\n", encoding="utf-8")
    budget_cli.main(["--ledger", str(path)])
    assert "WARNING" in capsys.readouterr().out
