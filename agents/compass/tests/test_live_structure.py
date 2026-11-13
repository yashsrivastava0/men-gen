"""Batch H, the structure replay (``mde_compass.live_structure``), offline: the real exports are the input, the mock
handlers answer, and a live run is refused without the outer-environment switches. Nothing here reaches a model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.live_structure import REPLAYS, load_replay, main, render_structure


@pytest.mark.parametrize("name", ["delhi", "pune", "hospital"])
def test_the_two_real_exports_load_as_a_brief_without_a_structure_and_their_active_facts(
    name: str,
) -> None:
    replay = load_replay(name)
    assert REPLAYS[name].is_file()
    assert replay.brief.scope and replay.brief.roles and replay.facts
    assert not (replay.brief.modules or replay.brief.screens or replay.brief.flows)
    assert all(f.status == "active" for f in replay.facts)
    assert replay.tier in ("quick", "standard", "thorough")


def test_a_mock_replay_drafts_a_structure_inside_the_band_and_writes_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--replay", "pune", "--band", "medium", "--mock", "--quiet"],
        env={},
        ledger_path=tmp_path / "ledger.sqlite3",
        out_dir=tmp_path,
    )
    assert code == 0
    reports = list(tmp_path.glob("H-pune-*-medium-mock-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["mode"] == "mock" and report["fallbacks"] == 0 and report["errors_left"] == 0
    assert report["cost_micros"] == 0
    counts = report["counts"]
    assert counts["modules"] >= 3 and 15 <= counts["screens"] <= 40 and counts["flows"] >= 1
    assert report["calls"]["outline"] and report["calls"]["module"] and report["calls"]["review"]
    assert set(report["structure"]) == {"modules", "screens", "flows", "entities"}
    assert report["research"]["depth"] == "standard"
    assert "MODULE MOD-" in report["text"] and "ENTITY ENT-" in report["text"]
    assert "report:" in capsys.readouterr().out


def test_the_rendered_structure_names_screens_roles_flows_and_data() -> None:
    from mde_compass.engine.structure_testing import sample_app

    text = render_structure(sample_app())
    for needle in ("MODULE MOD-001", "SCREEN SCR-001", "FLOW FLW-001", "ENTITY ENT-001", "Patient"):
        assert needle in text


def test_a_live_run_is_refused_without_the_switches_and_a_dry_run_calls_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.sqlite3"
    refused = main(["--replay", "pune"], env={}, ledger_path=ledger, out_dir=tmp_path)
    err = capsys.readouterr().err
    assert refused == 2 and "REFUSED" in err and "LLM_MODE" in err
    assert not list(tmp_path.glob("H-*.json"))
    dry = main(["--replay", "pune", "--dry-run"], env={}, ledger_path=ledger, out_dir=tmp_path)
    out = capsys.readouterr().out
    assert dry == 0 and "dry run: nothing was called" in out and not list(tmp_path.glob("H-*.json"))


def test_a_bad_replay_or_allowance_is_a_usage_error(tmp_path: Path) -> None:
    assert main(["--replay", str(tmp_path / "nope.json"), "--mock"], env={}) == 2
    assert main(["--replay", "pune", "--mock", "--allowance-usd", "lots"], env={}) == 2


def test_the_enterprise_replay_has_ten_roles_and_a_mock_draft_reaches_the_large_band(
    tmp_path: Path,
) -> None:
    replay = load_replay("hospital")
    assert len(replay.brief.roles) >= 8 and len(replay.brief.scope) >= 10
    code = main(
        ["--replay", "hospital", "--band", "large", "--mock", "--quiet"],
        env={},
        ledger_path=tmp_path / "ledger.sqlite3",
        out_dir=tmp_path,
    )
    assert code == 0
    report = json.loads(
        next(tmp_path.glob("H-hospital-*-large-mock-*.json")).read_text(encoding="utf-8")
    )
    assert report["fallbacks"] == 0 and report["counts"]["modules"] >= 8
    assert report["counts"]["screens"] >= 40 and report["research"]["depth"] == "deep"
