"""The batch run script: refuses without the owner's switches, estimates offline, and a mock run never touches the repo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_research_agent import live_runs as runs

REPO = Path(__file__).resolve().parents[3]


def test_preflight_refuses_without_the_owners_switches() -> None:
    problems = runs.preflight_live({})
    assert any("live" in p for p in problems)
    assert any("ALLOW_LIVE_LLM" in p for p in problems)


def test_preflight_still_refuses_with_only_the_mode_set() -> None:
    problems = runs.preflight_live({"LLM_MODE": "live"})
    assert any("ALLOW_LIVE_LLM" in p for p in problems)
    assert not any("model mode" in p for p in problems)


def test_the_forbid_switch_is_honoured() -> None:
    problems = runs.preflight_live(
        {"LLM_MODE": "live", "ALLOW_LIVE_LLM": "1", "MDE_FORBID_LIVE": "1"}
    )
    assert any("MDE_FORBID_LIVE" in p for p in problems)


def test_dry_run_estimates_and_calls_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert runs.main(["--run", "all", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for name in runs.RUNS:
        assert f"{name}:" in out
    assert "estimated total USD" in out


def test_a_live_run_without_the_switches_exits_without_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # the root test fixture forces mock mode and removes the second key, so this must refuse
    assert runs.main(["--run", "quick"]) == 2
    assert "cannot run live" in capsys.readouterr().err


def test_a_mock_run_writes_only_under_the_given_folder(tmp_path: Path) -> None:
    before = {p for p in (REPO / "agents" / "research").rglob("*") if "examples" in p.parts}
    assert runs.main(["--run", "quick", "--mock", "--out", str(tmp_path)]) == 0
    out = tmp_path / "examples" / "quick"
    research = json.loads((out / "research.json").read_text(encoding="utf-8"))
    assert research["schema"] == "research.v4" and research["items"]
    assert (out / "events.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads((out / "evidence.json").read_text(encoding="utf-8"))["events"].endswith(
        "(see events.jsonl)"
    )
    summary = (tmp_path / "summaries" / "quick.md").read_text(encoding="utf-8")
    assert summary.startswith("# Research run: quick (mock)")
    assert "## Targets" in summary and "## What is usual" in summary
    after = {p for p in (REPO / "agents" / "research").rglob("*") if "examples" in p.parts}
    assert after == before, "a mock run must not write into the repo's examples"


def test_mock_and_replay_default_to_a_temp_folder_not_the_repo() -> None:
    import argparse

    for flags in ({"mock": True, "replay": False}, {"mock": False, "replay": True}):
        out, summaries = runs.destinations(argparse.Namespace(out=None, **flags))
        assert REPO not in out.parents and REPO not in summaries.parents
    out, _ = runs.destinations(argparse.Namespace(out=None, mock=False, replay=False))
    assert out == runs.EXAMPLES


def test_evidence_text_is_truncated_in_the_saved_copy() -> None:
    evidence = {"pages": [{"text": "x" * 5000}, {"text": "short"}], "events": [1, 2, 3]}
    small = runs.truncate_evidence(evidence)
    assert small["pages"][0]["text"].endswith("[truncated]")
    assert len(small["pages"][0]["text"]) < 2100 and small["pages"][1]["text"] == "short"
    assert len(evidence["pages"][0]["text"]) == 5000, "the original is untouched"


def test_verdicts_compare_with_the_targets() -> None:
    good = dict.fromkeys(runs.TARGETS, 1.0)
    assert set(runs.verdicts(good).values()) == {"pass"}
    bad = {**good, "slots_filled_ratio": 0.2, "recent_24m_ratio_of_dated": None}
    v = runs.verdicts(bad)
    assert v["slots_filled_ratio"] == "FAIL" and v["recent_24m_ratio_of_dated"] == "n/a"
