"""Saving a finished run under docs/: what is copied, the numbers file, the README, the safety check, the index.

The run is a real mock run (``synth:small``, offline). Tests that change a run work on a private copy."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]

# The planted texts are built at run time: this file must not match the repo guards itself.
PHRASE = "allow" + "_live"
OPENAI_KEY = "sk-" + "proj-" + "A1b2C3d4E5" * 4
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"

SUMMARY_KEYS = {
    "schema", "run_id", "input", "compass_id", "synth", "program_id", "created_at", "updated_at",
    "profile", "status", "partial_reason", "offline", "mode", "cost_micros", "cost_usd", "run_spend_micros", "calls",
    "by_kind", "pack", "tasks", "validators", "banners", "not_copied",
}  # fmt: skip

Digest = Callable[[Path], dict[str, str]]
Plant = Callable[[Path, str, str], None]


def listed_files(run: Path) -> set[str]:
    manifest = json.loads((run / "pack" / "MANIFEST.json").read_text(encoding="utf-8"))
    return set(manifest["files"]) | {"MANIFEST.json", "index.json", "build/build-plan.json"}


def files_under(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}


def test_only_the_files_the_manifest_lists_are_copied(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    (run_copy / "pack" / "screens" / "STALE-after-reassemble.md").write_text(
        "old", encoding="utf-8"
    )
    (run_copy / "pack" / "old-folder").mkdir()
    (run_copy / "pack" / "old-folder" / "leftover.md").write_text("old", encoding="utf-8")
    out = tmp_path / "out" / "mock-2026-11-14"

    result = exporter.export_run(run_copy, out)

    dest = out / run_copy.name
    assert result.dest == dest and not result.refused and result.skipped == []
    assert files_under(dest / "pack") == listed_files(run_copy)
    for rel in listed_files(run_copy):
        assert (dest / "pack" / rel).read_bytes() == (run_copy / "pack" / rel).read_bytes()
    assert not (dest / "pack" / "screens" / "STALE-after-reassemble.md").exists()
    assert not (dest / "pack" / "old-folder").exists()
    top = {p.name for p in dest.iterdir()}
    assert top == {
        "pack",
        "evidence",
        "graph.json",
        "run.json",
        "report.json",
        "summary.json",
        "README.md",
    }
    assert {p.name for p in (dest / "evidence").iterdir()} == {
        p.name for p in (run_copy / "evidence").glob("*.json")
    }
    assert (dest / "graph.json").read_bytes() == (run_copy / "graph.json").read_bytes()
    assert (dest / "run.json").read_bytes() == (run_copy / "run.json").read_bytes()
    assert not (dest.parent / f".export-{run_copy.name}.tmp").exists()


def test_a_changed_pack_file_is_refused_not_copied(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    with (run_copy / "pack" / "AGENTS.md").open("a", encoding="utf-8") as fh:
        fh.write("\nedited after assembly\n")
    with pytest.raises(exporter.ExportError, match="does not match its hash"):
        exporter.export_run(run_copy, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_summary_json_is_the_small_numbers_file(
    exporter: ModuleType, run_report: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    result = exporter.export_run(run_copy, tmp_path / "out", label="the small synthetic input")
    summary = json.loads((result.dest / "summary.json").read_text(encoding="utf-8"))
    assert set(summary) == SUMMARY_KEYS
    assert summary["schema"] == "spec.export.summary.v1"
    assert summary["run_id"] == run_copy.name
    assert summary["input"] == "the small synthetic input"
    assert summary["mode"] == "mock" and summary["offline"] is True
    assert summary["status"] == "approved" and summary["partial_reason"] is None
    assert summary["profile"] == "compact"
    assert isinstance(summary["cost_micros"], int) and summary["cost_usd"] == "0.0000"
    rep = run_report.report(run_copy)
    assert summary["calls"] == rep["calls_total"] > 0
    assert set(summary["by_kind"]) == set(rep["by_kind"])
    for kind, row in summary["by_kind"].items():
        assert set(row) == {"calls", "first_try_pass", "repair_rate", "repaired", "fallback"}
        assert row["first_try_pass"] == rep["by_kind"][kind]["first_try_pass"]
    assert set(summary["pack"]) >= {
        "files",
        "tokens",
        "content_hash",
        "checked_by",
        "critic_vendor",
    }
    assert summary["pack"]["files"] == len(listed_files(run_copy)) - 3
    assert set(summary["tasks"]) == {"count", "max_read_tokens", "budget", "over_budget"}
    assert summary["tasks"]["max_read_tokens"] <= summary["tasks"]["budget"]
    assert summary["validators"]["ok"] is True
    assert summary["validators"]["by_validator"]  # named, not empty (the report bug)
    assert summary["not_copied"] == []
    assert json.loads((result.dest / "report.json").read_text(encoding="utf-8")) == rep


def test_the_readme_says_mock_in_plain_words(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out" / "mock-2026-11-14"
    result = exporter.export_run(run_copy, out, label="synth:small")
    readme = (result.dest / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Spec output: synth:small (MOCK)")
    assert "**MOCK.**" in readme and "No model was called and nothing was spent" in readme
    assert "**LIVE.**" not in readme
    assert f"`{run_copy.name}`" in readme
    for pointer in ("pack/INDEX.md", "pack/screens/", "pack/contract/", "evidence/self-check.json"):
        assert pointer in readme
    assert "uv run python -m mde_spec_core check" in readme and "/pack" in readme
    assert "Not complete" not in readme


def test_the_readme_says_live_and_the_cost_for_a_live_run(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    rec = json.loads((run_copy / "run.json").read_text(encoding="utf-8"))
    rec["offline"] = False
    (run_copy / "run.json").write_text(json.dumps(rec), encoding="utf-8")
    result = exporter.export_run(run_copy, tmp_path / "out")
    readme = (result.dest / "README.md").read_text(encoding="utf-8")
    assert (
        "(LIVE)" in readme.splitlines()[0] and "**LIVE.**" in readme and "**MOCK.**" not in readme
    )
    assert result.mode == "live"
    assert f"USD {result.summary['cost_usd']}" in readme


def test_the_default_folder_is_named_from_the_mode_and_the_day(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    rec = json.loads((run_copy / "run.json").read_text(encoding="utf-8"))
    assert exporter.default_out(rec, "mock", tmp_path) == (
        tmp_path / "docs" / "spec-agent" / "outputs" / f"mock-{rec['created_at'][:10]}"
    )


def test_a_mode_that_disagrees_with_the_run_is_refused(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    with pytest.raises(exporter.ExportError, match="MOCK, not LIVE"):
        exporter.export_run(run_copy, tmp_path / "out", mode="live")
    assert exporter.export_run(run_copy, tmp_path / "out", mode="mock").mode == "mock"


def test_a_run_that_is_still_going_is_refused(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    rec = json.loads((run_copy / "run.json").read_text(encoding="utf-8"))
    rec["status"] = "running"
    (run_copy / "run.json").write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not finished"):
        exporter.export_run(run_copy, tmp_path / "out")


def test_the_exported_pack_passes_the_pack_check(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    result = exporter.export_run(run_copy, tmp_path / "out")
    done = subprocess.run(
        [sys.executable, "-m", "mde_spec_core", "check", str(result.dest / "pack")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "RESULT: OK" in done.stdout


def test_a_destination_that_is_not_an_export_is_never_replaced(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out" / run_copy.name
    dest.mkdir(parents=True)
    (dest / "my-notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not a Spec export"):
        exporter.export_run(run_copy, tmp_path / "out")
    assert (dest / "my-notes.txt").read_text(encoding="utf-8") == "keep me"


def test_exporting_twice_gives_the_same_bytes(
    exporter: ModuleType, run_copy: Path, tmp_path: Path, digest: Digest
) -> None:
    first = digest(exporter.export_run(run_copy, tmp_path / "out").dest)
    second = digest(exporter.export_run(run_copy, tmp_path / "out").dest)
    assert first == second


# --- the safety check ------------------------------------------------------------------------------------------------

PLANTS = {
    "forbidden phrase": (PHRASE, "a phrase the repo guard forbids"),
    "forbidden phrase in other case": (PHRASE.upper(), "a phrase the repo guard forbids"),
    "openai key": (f"use {OPENAI_KEY} here", "key-like text (openai_key)"),
    "aws key": (f"id {AWS_KEY}", "key-like text (aws_access_key)"),
    "github token": (f"token {GITHUB_TOKEN}", "key-like text (github_token)"),
    "key split by a zero-width character": (
        f"use {OPENAI_KEY[:14]}​{OPENAI_KEY[14:]} here",
        "key-like text (openai_key)",
    ),
    "key with the allow marker": (
        f"{OPENAI_KEY}  secret-scan: allow",
        "key-like text (openai_key)",
    ),
}


@pytest.mark.parametrize("name", PLANTS)
def test_a_planted_phrase_or_key_is_refused_and_no_byte_changes(
    name: str,
    exporter: ModuleType,
    run_copy: Path,
    plant: Plant,
    tmp_path: Path,
    digest: Digest,
) -> None:
    text, why = PLANTS[name]
    plant(run_copy, "AGENTS.md", text)
    before = digest(run_copy)
    out = tmp_path / "out"

    result = exporter.export_run(run_copy, out)

    assert digest(run_copy) == before  # the source is untouched
    assert result.refused and [s.path for s in result.skipped] == ["pack/AGENTS.md"]
    assert why in result.skipped[0].reason
    assert files_under(result.dest) == {"EXPORT-NOTES.md"}  # strict: nothing else was written
    notes = (result.dest / "EXPORT-NOTES.md").read_text(encoding="utf-8")
    assert "not copied: pack/AGENTS.md, " + why in notes
    assert text not in notes and OPENAI_KEY not in notes  # a match is never echoed
    assert exporter.export_and_report(run_copy, out, refresh=False) == 1


def test_allow_skip_copies_the_rest_and_records_what_it_left_out(
    exporter: ModuleType,
    run_copy: Path,
    plant: Plant,
    tmp_path: Path,
    digest: Digest,
) -> None:
    plant(run_copy, "AGENTS.md", PHRASE)
    plant(run_copy, "INDEX.md", f"key {OPENAI_KEY}")
    before = digest(run_copy)

    result = exporter.export_run(run_copy, tmp_path / "out", allow_skip=True)

    assert digest(run_copy) == before
    assert not result.refused
    assert {s.path for s in result.skipped} == {"pack/AGENTS.md", "pack/INDEX.md"}
    dest = result.dest
    assert not (dest / "pack" / "AGENTS.md").exists() and not (dest / "pack" / "INDEX.md").exists()
    assert files_under(dest / "pack") == listed_files(run_copy) - {"AGENTS.md", "INDEX.md"}
    for rel in files_under(dest / "pack"):  # what was copied is byte for byte the engine's file
        assert (dest / "pack" / rel).read_bytes() == (run_copy / "pack" / rel).read_bytes()
    notes = (dest / "EXPORT-NOTES.md").read_text(encoding="utf-8")
    assert "not copied: pack/AGENTS.md, " in notes and "not copied: pack/INDEX.md, " in notes
    assert "Some files were not copied" in notes
    assert "Not complete" in (dest / "README.md").read_text(encoding="utf-8")
    summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    assert {n["path"] for n in summary["not_copied"]} == {"pack/AGENTS.md", "pack/INDEX.md"}
    assert (
        exporter.export_and_report(run_copy, tmp_path / "out2", allow_skip=True, refresh=False) == 0
    )


def test_the_guard_patterns_come_from_the_repos_guard_tests(exporter: ModuleType) -> None:
    guard = exporter.load_guard(ROOT)
    assert guard.forbidden.search(PHRASE) and guard.forbidden.search("A Live " + "Switch")
    assert (
        ".md" in guard.text_suffixes
        and ".json" in guard.text_suffixes
        and "" in guard.text_suffixes
    )
    assert {n for n, _ in guard.secret_patterns} >= {"openai_key", "aws_access_key", "github_token"}
    assert exporter.scan_file(guard, "pack/x.md", b"An online shop for handmade candles.") == []
    assert exporter.scan_file(guard, "pack/x.md", b"sk-learn is a library") == []
    assert (
        exporter.scan_file(guard, "pack/x.bin", b"\xff\xfe\x00") != []
    )  # unscannable and not a known binary


def test_a_missing_guard_means_no_export(exporter: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(exporter.ExportError, match="guard tests"):
        exporter.load_guard(tmp_path)


# --- the index of the saved folders ----------------------------------------------------------------------------------


def make_second_run(
    run: Path, tmp_path: Path, plant_id: str, created_at: str, offline: bool
) -> Path:
    """A copy of ``run`` under another id, start time and mode (the pack is the same)."""
    other = tmp_path / "runs" / plant_id
    other.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run, other)
    rec = json.loads((other / "run.json").read_text(encoding="utf-8"))
    rec.update({"id": plant_id, "created_at": created_at, "offline": offline})
    (other / "run.json").write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    return other


def test_the_parent_index_lists_each_run_newest_first_and_is_deterministic(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    outputs = tmp_path / "outputs"
    (outputs / "report-only-folder").mkdir(parents=True)
    (outputs / "report-only-folder" / "x.report.json").write_text("{}", encoding="utf-8")
    newer = make_second_run(run_copy, tmp_path, "run_NEWER", "2999-01-01T00:00:00+00:00", False)
    assert (
        exporter.export_and_report(run_copy, outputs / "mock-2026-11-14", label="synth:small") == 0
    )
    assert exporter.export_and_report(newer, outputs / "live-2999-01-01", label="a live input") == 0

    index = outputs / "README.md"
    first = index.read_bytes()
    text = index.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| [")]
    assert len(rows) == 2
    assert "live-2999-01-01/run_NEWER" in rows[0] and "| LIVE | a live input |" in rows[0]
    assert f"mock-2026-11-14/{run_copy.name}" in rows[1] and "| MOCK | synth:small |" in rows[1]
    assert f"(mock-2026-11-14/{run_copy.name}/README.md)" in rows[1]
    assert "`report-only-folder`" in text  # a folder with numbers only is named, never dropped

    assert exporter.refresh_index(outputs).read_bytes() == first  # the same folders, the same bytes
    assert (
        exporter.export_and_report(run_copy, outputs / "mock-2026-11-14", label="synth:small") == 0
    )
    assert index.read_bytes() == first  # and a re-export changes nothing


def test_everything_the_exporter_writes_passes_the_repos_guards(
    exporter: ModuleType, run_copy: Path, tmp_path: Path
) -> None:
    """The README, notes, summary and index are held to the same rules as the pack."""
    guard = exporter.load_guard(ROOT)
    outputs = tmp_path / "outputs"
    assert exporter.export_and_report(run_copy, outputs / "mock-2026-11-14") == 0
    for path in outputs.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            rel = path.relative_to(outputs).as_posix()
            assert exporter.scan_file(guard, rel, data) == [], rel
