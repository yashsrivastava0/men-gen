"""``python -m mde_spec_agent bench export``: the same functions as the script, the run found under the Spec data
folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_spec_agent.__main__ import main


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def spec_data(mock_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPEC_DATA_DIR", str(mock_data_dir))
    return mock_data_dir


def test_bench_export_by_run_id(
    spec_data: Path, mock_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "outputs" / "mock-2026-11-14"
    code, text, err = run_cli(
        capsys,
        "bench",
        "export",
        "--run",
        mock_run.name,
        "--out",
        str(out),
        "--label",
        "synth:small",
    )
    assert code == 0, err
    assert f"exported {mock_run.name}" in text and "mode: MOCK, status: approved" in text
    dest = out / mock_run.name
    assert (dest / "pack" / "MANIFEST.json").is_file() and (dest / "README.md").is_file()
    summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    assert summary["input"] == "synth:small" and summary["mode"] == "mock"
    assert (tmp_path / "outputs" / "README.md").is_file()  # the parent index


def test_bench_export_no_index(
    spec_data: Path, mock_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "outputs" / "mock-2026-11-14"
    code, _, err = run_cli(
        capsys, "bench", "export", "--run", mock_run.name, "--out", str(out), "--no-index"
    )
    assert code == 0, err
    assert not (tmp_path / "outputs" / "README.md").exists()


def test_bench_export_accepts_a_run_folder_path(
    spec_data: Path, mock_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys, "bench", "export", "--run", str(mock_run), "--out", str(tmp_path / "o" / "m")
    )
    assert code == 0, err
    assert (tmp_path / "o" / "m" / mock_run.name / "summary.json").is_file()


def test_bench_export_unknown_run_is_a_usage_error(
    spec_data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys, "bench", "export", "--run", "run_NOPE", "--out", str(tmp_path / "o")
    )
    assert code == 2 and "no such run: run_NOPE" in err
    assert not (tmp_path / "o").exists()


def test_bench_export_mode_mismatch_exits_2(
    spec_data: Path, mock_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys,
        "bench",
        "export",
        "--run",
        mock_run.name,
        "--out",
        str(tmp_path / "o"),
        "--mode",
        "live",
    )
    assert code == 2 and "MOCK, not LIVE" in err
    assert not (tmp_path / "o").exists()


def test_bench_export_has_the_documented_options() -> None:
    from mde_spec_agent.__main__ import build_parser

    args = build_parser().parse_args(
        ["bench", "export", "--run", "run_X", "--mode", "mock", "--allow-skip", "--label", "x"]
    )
    assert args.run == "run_X" and args.mode == "mock" and args.allow_skip and args.label == "x"
    assert args.out is None and args.no_index is False
