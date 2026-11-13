"""The headless CLI: ``bench auto``, ``bench run``, ``serve`` (docs/api/spec-agent.md "Headless")."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from mde_spec_agent.__main__ import main
from mde_spec_agent.bench.inputs import discover_compass

YOGA = "quick-yoga-studio-bengaluru"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_run: Any, nine_dir: Path) -> Path:
    """Spec's data folder and Research's data folder point into tmp_path, so only the nine real exports are seen."""
    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "spec"))
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path))  # runs/ is where make_run writes
    monkeypatch.delenv("SPEC_INPUT_DIRS", raising=False)
    yoga = next(e for e in discover_compass([nine_dir]) if e.id == YOGA)
    make_run("rrn_yoga", export_hash=yoga.export_hash)
    return tmp_path


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_bench_auto_prints_the_proposals_in_plain_words(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run_cli(capsys, "bench", "auto")
    assert code == 0
    assert "Compass exports found: 9; Research runs found: 1" in out
    for expected in (
        "[compact] quick-yoga-studio-bengaluru",
        "research: rrn_yoga",
        "[standard] standard-produce-marketplace-nashik",
        "gap:      no Research run: Spec runs with a thin research lane and says so",
        "[deep] thorough-freight-fleet-india",
        "compass:  synth:big  (synthetic)",
        "[program] synthetic Uber Eats-class program",
    ):
        assert expected in out


def test_bench_auto_json(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "bench", "auto", "--json")
    assert code == 0
    data = json.loads(out)
    assert [p["compass_id"] for p in data["proposals"]] == [
        YOGA,
        "standard-produce-marketplace-nashik",
        "thorough-freight-fleet-india",
        "synth:big",
        "synth:program",
    ]
    assert data["proposals"][0]["research_id"] == "rrn_yoga"
    assert data["compass_found"] == 9 and data["research_found"] == 1 and data["skipped"] == []
    assert data["proposals"][3]["synthetic"] is True


def test_bench_auto_size_filter(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "bench", "auto", "--size", "deep", "--json")
    assert code == 0
    assert [p["compass_id"] for p in json.loads(out)["proposals"]] == [
        "thorough-freight-fleet-india",
        "synth:big",
    ]


def test_bench_auto_lists_skipped_files_and_never_crashes(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    extra = env / "extra"
    extra.mkdir()
    (extra / "junk.json").write_text("oops", encoding="utf-8")
    monkeypatch.setenv("SPEC_INPUT_DIRS", str(extra))
    code, out, _ = run_cli(capsys, "bench", "auto")
    assert code == 0 and "junk.json: not valid JSON" in out


def test_a_bad_size_is_a_usage_error(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bench", "auto", "--size", "huge"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_bench_run_exits_3_when_the_service_is_not_built(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        sys.modules, "mde_spec_agent.service", None
    )  # as if the module does not exist
    code, out, err = run_cli(capsys, "bench", "run", "--input", YOGA, "--offline")
    assert code == 3
    assert "the run service is not built yet" in err and out == ""


def test_bench_run_calls_the_service_with_the_best_matching_research(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def run_to_end(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"run_dir": "/x/run", "gates": {"g2": "awaiting"}, "pack_hash": "abc"}

    fake = types.ModuleType("mde_spec_agent.service")
    fake.run_to_end = run_to_end  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mde_spec_agent.service", fake)
    code, out, _ = run_cli(capsys, "bench", "run", "--input", YOGA, "--offline")
    assert code == 0
    (kw,) = calls
    assert kw["synth"] is None and kw["offline"] is True
    assert Path(kw["compass_path"]).name == f"{YOGA}.json"
    assert Path(kw["research_path"]) == env / "runs" / "rrn_yoga" / "research.json"
    assert (
        "research: rrn_yoga (complete)" in out
        and "pack hash: abc" in out
        and "run folder: /x/run" in out
    )


def test_bench_run_accepts_a_path_a_synthetic_name_and_compass_only(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, nine_dir: Path
) -> None:
    calls: list[dict[str, Any]] = []

    async def run_to_end(**kw: Any) -> str:  # an async service is awaited
        calls.append(kw)
        return "done"

    fake = types.ModuleType("mde_spec_agent.service")
    fake.run_to_end = run_to_end  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mde_spec_agent.service", fake)

    assert run_cli(capsys, "bench", "run", "--input", "synth:program")[0] == 0
    assert (
        calls[-1]["synth"] == "program"
        and calls[-1]["compass_path"] is None
        and calls[-1]["research_path"] is None
    )
    assert calls[-1]["offline"] is False

    path = nine_dir / f"{YOGA}.json"
    assert run_cli(capsys, "bench", "run", "--input", str(path), "--no-research")[0] == 0
    last: dict[str, Any] = dict(calls[-1])
    assert last["research_path"] is None and Path(last["compass_path"]) == path

    explicit = env / "runs" / "rrn_yoga"
    assert run_cli(capsys, "bench", "run", "--input", YOGA, "--research", str(explicit))[0] == 0
    last = dict(calls[-1])
    assert Path(last["research_path"]) == explicit / "research.json"


def test_bench_run_refuses_bad_input_with_exit_2(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        sys.modules, "mde_spec_agent.service", None
    )  # input is checked before the service
    assert run_cli(capsys, "bench", "run", "--input", "no-such-thing")[0] == 2
    assert run_cli(capsys, "bench", "run", "--input", "synth:nope")[0] == 2
    assert (
        run_cli(capsys, "bench", "run", "--input", YOGA, "--research", str(env / "missing.json"))[0]
        == 2
    )
    assert run_cli(capsys, "bench", "run", "--input", "synth:big", "--research", str(env))[0] == 2


def test_bench_run_reports_a_failing_run_with_exit_1(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_to_end(**_kw: Any) -> None:
        raise RuntimeError("the budget is spent")

    fake = types.ModuleType("mde_spec_agent.service")
    fake.run_to_end = run_to_end  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mde_spec_agent.service", fake)
    code, _, err = run_cli(capsys, "bench", "run", "--input", "synth:big", "--offline")
    assert code == 1 and "the budget is spent" in err


def test_bench_run_refuses_an_export_that_is_not_ready(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, nine_dir: Path
) -> None:
    data = json.loads((nine_dir / f"{YOGA}.json").read_text(encoding="utf-8"))
    data["brief"]["scope"] = []
    path = env / "nofeature.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code, _, err = run_cli(capsys, "bench", "run", "--input", str(path))
    assert code == 2 and "not ready" in err and "at least one feature" in err


def test_serve_refuses_a_non_loopback_host(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run_cli(capsys, "serve", "--host", "0.0.0.0")  # noqa: S104
    assert code == 2 and "non-loopback" in err


def test_serve_exits_3_when_the_app_is_not_built(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "mde_spec_agent.api.main", None)
    code, _, err = run_cli(capsys, "serve")
    assert code == 3 and "not built yet" in err


def test_serve_hands_host_and_port_to_the_app(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    fake = types.ModuleType("mde_spec_agent.api.main")
    fake.main = lambda argv: seen.append(argv) or 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mde_spec_agent.api.main", fake)
    assert run_cli(capsys, "serve", "--port", "8131")[0] == 0
    assert seen == [["--host", "127.0.0.1", "--port", "8131"]]
