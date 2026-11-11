import json
from pathlib import Path

import pytest
from support.testing import AS_OF, load_fixture

from mde_research_agent.run import main, preflight_live, run_export, write_outputs

QUICK = (
    Path(__file__).resolve().parent / "fixtures" / "exports" / "quick-yoga-studio-bengaluru.json"
)


def test_live_refuses_without_live_mode() -> None:
    problems = preflight_live({})
    assert any("model mode" in p for p in problems)
    owner_env = {"LLM_MODE": "live"}
    assert not any("model mode" in p for p in preflight_live(owner_env))
    assert any(
        "MDE_FORBID_LIVE" in p for p in preflight_live({**owner_env, "MDE_FORBID_LIVE": "1"})
    )


def test_the_live_flag_exits_with_a_plain_reason_when_not_allowed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODE", "mock")
    assert main(["--export", str(QUICK), "--live"]) == 2
    assert "cannot run live" in capsys.readouterr().err


async def test_mock_run_writes_the_three_files(tmp_path: Path) -> None:
    res, evidence, events = await run_export(
        load_fixture("quick-yoga-studio-bengaluru"), mock=True, program_id="prog_cli", as_of=AS_OF
    )
    write_outputs(tmp_path / "out", res, evidence, events)
    assert json.loads((tmp_path / "out" / "research.json").read_text())["schema"] == "research.v5"
    assert json.loads((tmp_path / "out" / "research_evidence.json").read_text())["pages"]
    assert len((tmp_path / "out" / "events.jsonl").read_text().splitlines()) == len(events) > 20


def test_a_changed_export_is_refused_with_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = load_fixture("quick-yoga-studio-bengaluru")
    data["brief"]["_hash"] = "0" * 64
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))
    assert main(["--export", str(p), "--mock"]) == 2
    assert "refused" in capsys.readouterr().err
