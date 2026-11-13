"""Shared builders for the bench tests: a fake Research ``runs/`` folder, plain JSON files only."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

NINE = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"

MakeRun = Callable[..., Path]


@pytest.fixture
def nine_dir() -> Path:
    return NINE


@pytest.fixture
def make_run(tmp_path: Path) -> MakeRun:
    """``make_run(run_id, ...)`` writes ``tmp_path/runs/<run_id>/run.json`` (and research.json) like Research does."""
    runs = tmp_path / "runs"

    def build(
        run_id: str,
        *,
        status: str = "complete",
        export_hash: str | None = "",
        brief_id: str = "ses_X",
        brief_hash: str = "bh",
        created_at: str = "2026-11-13T10:00:00.000000Z",
        research: bool = True,
        run_json: bool = True,
        runs_dir: Path | None = None,
    ) -> Path:
        folder = (runs_dir or runs) / run_id
        folder.mkdir(parents=True, exist_ok=True)
        if run_json:
            row: dict[str, object] = {
                "run_id": run_id,
                "program_id": brief_id,
                "status": status,
                "brief_hash": brief_hash,
                "brief_version": 3,
                "result_status": status,
                "created_at": created_at,
            }
            if export_hash is not None:
                row["export_sha256"] = export_hash
            (folder / "run.json").write_text(json.dumps(row), encoding="utf-8")
        if research:
            doc = {
                "schema": "research.v5",
                "program_id": brief_id,
                "brief_hash": brief_hash,
                "status": status,
            }
            (folder / "research.json").write_text(json.dumps(doc), encoding="utf-8")
        return folder

    build.runs_dir = runs  # type: ignore[attr-defined]
    return build
