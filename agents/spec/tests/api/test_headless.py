"""The headless helper behind ``bench run``: the whole flow in one call, gates auto-approved ONLY there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_spec_agent.__main__ import main as cli_main
from mde_spec_agent.service import run_to_end

INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"


@pytest.fixture(autouse=True)
def _data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "headless"))


def test_run_to_end_returns_the_documented_summary() -> None:
    res = run_to_end(compass_path=INPUTS / "quick-yoga-studio-bengaluru.json", offline=True)
    assert set(res) >= {"run_id", "run_dir", "status", "gates", "pack_hash"}
    assert res["status"] == "approved" and res["gates"] == {"g2": "approved (headless)"}
    run_dir = Path(res["run_dir"])
    assert (run_dir / "pack" / "MANIFEST.json").is_file()
    rec = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert rec["approved_hash"] == rec["content_hash"] == res["content_hash"]


def test_a_deep_input_goes_through_g15_headlessly() -> None:
    res = run_to_end(compass_path=INPUTS / "thorough-freight-fleet-india.json", offline=True)
    assert res["status"] == "approved" and set(res["gates"]) == {"g15", "g2"}
    rec = json.loads((Path(res["run_dir"]) / "run.json").read_text(encoding="utf-8"))
    assert rec["checked_by"] == ["self_check", "verify"]


def test_a_live_run_is_refused_without_keys_and_nothing_is_spent() -> None:
    from mde_spec_agent.service import NotReady

    with pytest.raises(NotReady):
        run_to_end(compass_path=INPUTS / "quick-yoga-studio-bengaluru.json", offline=False)


def test_bench_run_cli_uses_it(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli_main(["bench", "run", "--input", "quick-yoga-studio-bengaluru", "--offline"])
    out = capsys.readouterr().out
    assert code == 0 and "status: approved" in out and "pack hash:" in out
