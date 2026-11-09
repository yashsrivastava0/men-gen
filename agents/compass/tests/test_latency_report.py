from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[3])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mde_compass.store.ports import CallLogEntry  # noqa: E402
from scripts.compass_latency_report import _format_bar, generate_report  # noqa: E402


def test_format_bar_calculates_correct_proportions() -> None:
    bar = _format_bar(0, 50, 100, width=10)
    assert bar == "[=====     ]"

    bar_end = _format_bar(50, 50, 100, width=10)
    assert bar_end == "[     =====]"


def test_generate_report_with_parallel_calls() -> None:
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    entries = [
        CallLogEntry(
            call_id="c1",
            spec_id="cmp.plan_questions",
            latency_ms=2000,
            at=now,
            turn_id="turn_1",
            tokens_in=100,
            tokens_out=200,
        ),
        CallLogEntry(
            call_id="c2",
            spec_id="cmp.situate",
            latency_ms=1500,
            at=now,
            turn_id="turn_1",
            tokens_in=50,
            tokens_out=150,
        ),
    ]

    report = generate_report("ses_test", entries)
    assert "ses_test" in report
    assert "Turn #1: turn_1" in report
    assert "cmp.plan_questions" in report
    assert "cmp.situate" in report
    assert "Sequential:   3500ms" in report
    assert "Peak Concurrency: 2x" in report
    assert "Saved:  1500ms" in report
