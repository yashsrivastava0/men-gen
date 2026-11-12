"""The inputs folder skips a file only for a reason that is true: a real bug is never reported as "not a Compass export"."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mde_research_agent.catalog import load_catalog
from mde_research_agent.inputs import discover
from mde_research_agent.inputs.discover import InputsStore
from mde_research_agent.inputs.estimates import Estimates

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAT = load_catalog()


def good() -> dict:
    return json.loads(
        (FIXTURES / "formats" / "format1_no_baseline.json").read_text(encoding="utf-8")
    )


def scan(tmp_path: Path, files: dict[str, object]):  # type: ignore[no-untyped-def]
    for name, obj in files.items():
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    return InputsStore(tmp_path, CAT, Estimates()).scan()


def test_the_three_reasons_a_json_file_can_be_skipped_are_each_the_true_one(tmp_path: Path) -> None:
    empty = good()
    empty["brief"].update(research_requests=[], domain_facts=[], idea={}, summary="")
    empty.pop("consumer_views")
    result = scan(
        tmp_path, {"hello.json": {"hello": 1}, "nobrief.json": {"export": {}}, "empty.json": empty}
    )
    assert {s.name: s.reason for s in result.skipped} == {
        "hello.json": "not_a_compass_export",
        "nobrief.json": "not_a_compass_export",
        "empty.json": "unusable_brief",
    }
    assert all(s.text for s in result.skipped) and result.valid == []


def test_an_unverified_or_older_export_is_listed_not_skipped(tmp_path: Path) -> None:
    result = scan(tmp_path, {"old.json": good()})
    assert result.skipped == [] and [e.preview["input_binding"] for e in result.valid] == [
        "unverified_no_baseline"
    ]


def test_an_unexpected_reader_error_is_its_own_reason_with_the_class_name_and_a_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("a bug that must not look like a bad file")

    monkeypatch.setattr(discover, "read_export", boom)
    with caplog.at_level(logging.WARNING, logger=discover.log.name):
        result = scan(tmp_path, {"x.json": good()})
    (skipped,) = result.skipped
    assert skipped.reason == "unreadable_export" and "RuntimeError" in skipped.text
    assert "not a Compass export" not in skipped.text
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
    assert not any(
        "must not look like" in r.getMessage() for r in caplog.records
    )  # never the error's text
