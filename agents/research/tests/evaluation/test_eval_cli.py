"""The eval command line prints its report as JSON (a live planted-error run once lost its result to a Decimal)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from support.testing import load_fixture, run_engine

from mde_research_agent.eval.__main__ import main


@pytest.mark.parametrize("what", ["planted", "probe"])
def test_the_command_prints_a_json_report_with_the_cost_in_dollars(
    what: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = asyncio.run(run_engine(load_fixture("standard-produce-marketplace-nashik")))
    research, evidence = tmp_path / "research.json", tmp_path / "evidence.json"
    research.write_text(json.dumps(run.research), encoding="utf-8")
    evidence.write_text(json.dumps(run.evidence), encoding="utf-8")
    brief = tmp_path / "input.json"
    brief.write_text(json.dumps({"idea": {"summary": "A market"}}), encoding="utf-8")
    argv = [what, "--mock", "--research", str(research), "--evidence", str(evidence)]
    if what == "probe":
        argv += ["--input", str(brief)]
    code = main(argv)
    out = capsys.readouterr().out
    report = json.loads(out[out.index("{") :])
    assert code in (0, 1) and "cost_usd" in report
    if what == "probe":
        assert report["brief_given"] is True
    else:
        assert "discrimination" in report and "partial_recheck" in report
