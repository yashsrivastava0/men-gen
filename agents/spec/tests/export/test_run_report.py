"""``scripts/spec_run_report.py``: the validator counts are read from where the self-check writes them
(``counts.by_validator`` and ``counts.by_severity``), not from the top level of ``evidence/self-check.json``."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType


def write_run(run: Path, self_check: dict[str, object]) -> Path:
    (run / "evidence").mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps({"id": "run_X", "status": "approved"}), encoding="utf-8"
    )
    (run / "evidence" / "self-check.json").write_text(json.dumps(self_check), encoding="utf-8")
    return run


def test_validator_counts_are_read_from_counts(run_report: ModuleType, tmp_path: Path) -> None:
    run = write_run(
        tmp_path / "run_X",
        {
            "ok": False,
            "counts": {
                "by_severity": {"blocker": 1, "warning": 2, "info": 0},
                "by_validator": {"V-04": 1, "V-30": 2},
            },
        },
    )
    validators = run_report.report(run)["validators"]
    assert validators == {
        "by_severity": {"blocker": 1, "warning": 2, "info": 0},
        "by_validator": {"V-04": 1, "V-30": 2},
        "ok": False,
    }


def test_the_old_top_level_spelling_still_reads(run_report: ModuleType, tmp_path: Path) -> None:
    run = write_run(
        tmp_path / "run_X",
        {"ok": True, "by_validator": {"V-36": 1}, "by_severity": {"info": 1}},
    )
    validators = run_report.report(run)["validators"]
    assert validators["by_validator"] == {"V-36": 1} and validators["by_severity"] == {"info": 1}


def test_a_run_without_a_self_check_gives_none_not_a_crash(
    run_report: ModuleType, tmp_path: Path
) -> None:
    run = tmp_path / "run_X"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"id": "run_X"}), encoding="utf-8")
    assert run_report.report(run)["validators"] == {
        "by_severity": None,
        "by_validator": None,
        "ok": None,
    }


def test_the_report_of_a_real_mock_run_names_its_validators(
    run_report: ModuleType, mock_run: Path
) -> None:
    rep = run_report.report(mock_run)
    counts = json.loads((mock_run / "evidence" / "self-check.json").read_text(encoding="utf-8"))[
        "counts"
    ]
    assert rep["validators"]["by_validator"] == counts["by_validator"] != {}
    assert rep["validators"]["by_severity"] == counts["by_severity"]
    assert rep["cost_micros"] == 0 and rep["cost_usd"] == 0.0  # existing keys kept, micros added
    assert {"run_id", "status", "profile", "offline", "calls_total", "by_kind", "banners"} <= set(
        rep
    )
