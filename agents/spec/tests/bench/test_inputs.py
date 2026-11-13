"""Discovery of Compass exports and Research runs (doc 19 4.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mde_spec_agent.bench.inputs import (
    discover_compass,
    discover_compass_report,
    discover_research,
)

# id -> (format, tier, size band, band source, profile, drift flags, in-scope features, screens, fixture)
# Read for plausibility: delhi and pune are the old format 1 and have no structure (0 screens); the hospital file has
# no structure either; the five format-2 files with a structure have their screens counted; "thorough freight" is
# the biggest (29 features, 132 screens); the three hand-made files are the `synthetic-` ones.
TABLE: dict[str, tuple[Any, ...]] = {
    "delhi-restaurant-delivery": (1, "standard", "medium", "tier", "standard", ("size_band_from_tier",), 3, 0, False),
    "hospital-chain-enterprise": (2, "thorough", "large", "tier", "deep", ("size_band_from_tier",), 13, 0, False),
    "pune-clinic-appointments": (1, "standard", "medium", "tier", "standard", ("size_band_from_tier",), 9, 0, False),
    "quick-yoga-studio-bengaluru": (2, "quick", "small", "structure", "compact", (), 6, 13, False),
    "standard-produce-marketplace-nashik": (2, "standard", "medium", "structure", "standard", (), 8, 28, False),
    "synthetic-big-site-safety-central-america": (2, "thorough", "large", "structure", "deep", (), 26, 57, True),
    "synthetic-medium-school-parents-dakar": (2, "standard", "medium", "structure", "standard", (), 13, 28, True),
    "synthetic-small-bike-repair-porto": (2, "quick", "small", "structure", "compact", (), 7, 15, True),
    "thorough-freight-fleet-india": (2, "thorough", "large", "structure", "deep", (), 29, 132, False),
}  # fmt: skip


def test_the_nine_exports_are_found_and_classified(nine_dir: Path) -> None:
    found = discover_compass_report([nine_dir])
    assert found.skipped == ()
    assert sorted(e.id for e in found.entries) == sorted(TABLE)
    for e in found.entries:
        fmt, tier, band, source, profile, drift, features, screens, fixture = TABLE[e.id]
        assert (e.format, e.tier, e.size_band, e.size_band_source) == (fmt, tier, band, source), (
            e.id
        )
        assert e.profile == profile and e.profile_source in ("derive", "fallback"), e.id
        assert e.drift_flags == drift, e.id
        assert (e.features, e.screens, e.weight, e.fixture) == (
            features,
            screens,
            features + screens,
            fixture,
        )
        assert e.readiness.ready and e.readiness.missing == ()
        assert (
            Path(e.path).name == f"{e.id}.json" and len(e.sha256) == 64 and len(e.export_hash) == 64
        )


def test_hashes_equal_what_research_recorded_for_the_yoga_run(nine_dir: Path) -> None:
    """Research's run.json holds ``export_sha256`` = canonical hash of the parsed export (not the file's byte hash)."""
    yoga = next(e for e in discover_compass([nine_dir]) if e.id == "quick-yoga-studio-bengaluru")
    assert yoga.export_hash == "e308cc47b69a5555224c9120b4f1f44053912baeeba64e51b806b0f671f93ee8"
    assert yoga.brief_hash == "a621e7b393ac6974e316347c39d70e384f68d5d87fc931735c0053a8963265f6"
    assert yoga.brief_id == "ses_01M72PT1S3G61C9WFTXJPWKC3N"
    assert yoga.sha256 != yoga.export_hash


def test_ids_are_stable_and_sorted_inside_a_directory(nine_dir: Path) -> None:
    first = [e.id for e in discover_compass([nine_dir])]
    assert first == [e.id for e in discover_compass([nine_dir])] == sorted(first)


def test_files_that_cannot_be_used_are_skipped_with_a_plain_reason(
    tmp_path: Path, nine_dir: Path
) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a json file at all", encoding="utf-8")
    (tmp_path / "huge.json").write_bytes(b" " * (8 * 1024 * 1024 + 1))
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe\x00\x01")
    (tmp_path / "subdir.json").mkdir()
    yoga = (nine_dir / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8")
    (tmp_path / "good.json").write_text(yoga, encoding="utf-8")

    found = discover_compass_report([tmp_path])
    assert [e.id for e in found.entries] == ["good"]
    reasons = {Path(s.path).name: s.reason for s in found.skipped}
    assert reasons == {
        "broken.json": "not valid JSON",
        "list.json": "JSON, but not an object: no Brief",
        "empty.json": "not a Compass export: it has no Brief in it",
        "other.json": "not a Compass export: it has no Brief in it",
        "huge.json": "larger than 8 MB",
        "binary.json": "not valid JSON",
    }


def test_missing_directories_and_duplicate_names_are_tolerated(
    tmp_path: Path, nine_dir: Path
) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    text = (nine_dir / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8")
    (copy / "quick-yoga-studio-bengaluru.json").write_text(text, encoding="utf-8")
    found = discover_compass_report([tmp_path / "does-not-exist", nine_dir, copy])
    assert len(found.entries) == 9
    assert [Path(s.path).parent for s in found.skipped] == [copy]
    assert "already listed" in found.skipped[0].reason
    assert discover_compass([]) == []


def test_an_export_with_no_feature_is_listed_as_not_ready_with_what_is_missing(
    tmp_path: Path, nine_dir: Path
) -> None:
    data = json.loads((nine_dir / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8"))
    data["brief"]["scope"] = []  # no scope: Compass must still ask what the app must do
    (tmp_path / "nofeature.json").write_text(json.dumps(data), encoding="utf-8")
    found = discover_compass_report([tmp_path])
    (entry,) = found.entries
    assert found.skipped == ()
    assert entry.readiness.ready is False
    assert entry.readiness.missing == (
        "What people must be able to do in the app: at least one feature that is in scope.",
    )


# --- Research --------------------------------------------------------------------------------------------------------


def test_research_runs_are_read_as_plain_json(make_run: Any) -> None:
    make_run("rrn_B", status="partial", export_hash="h1", created_at="2026-11-13T12:00:00Z")
    make_run("rrn_A", status="complete", export_hash="h1", created_at="2026-11-12T12:00:00Z")
    make_run(
        "rrn_C",
        status="failed",
        export_hash="h2",
        research=False,
        created_at="2026-11-13T13:00:00Z",
    )
    runs = discover_research(make_run.runs_dir)
    assert [r.id for r in runs] == ["rrn_A", "rrn_B", "rrn_C"]  # oldest first
    a, b, c = runs
    assert (a.status, a.input_hash, a.brief_id, a.brief_hash, a.brief_version) == (
        "complete",
        "h1",
        "ses_X",
        "bh",
        3,
    )
    assert a.has_research and a.usable and a.research_path.endswith("rrn_A/research.json")
    assert b.status == "partial" and b.usable
    assert c.status == "failed" and not c.has_research and not c.usable and c.research_path == ""


def test_research_tolerates_a_missing_folder_and_broken_files(
    make_run: Any, tmp_path: Path
) -> None:
    assert discover_research(tmp_path / "nope") == []
    runs_dir = make_run.runs_dir
    make_run("rrn_ok", export_hash="h")
    (runs_dir / "rrn_junk").mkdir()
    (runs_dir / "rrn_junk" / "run.json").write_text("{broken", encoding="utf-8")
    (runs_dir / "stray.txt").write_text("a file, not a run", encoding="utf-8")
    only_result = make_run("rrn_noRunJson", run_json=False, brief_id="ses_Y")
    assert only_result.is_dir()
    unreadable_result = make_run("rrn_badResult", export_hash="h3")
    (unreadable_result / "research.json").write_text("[", encoding="utf-8")
    got = {r.id: r for r in discover_research(runs_dir)}
    assert sorted(got) == [
        "rrn_badResult",
        "rrn_noRunJson",
        "rrn_ok",
    ]  # the junk folder has nothing readable
    assert got["rrn_noRunJson"].status == "complete" and got["rrn_noRunJson"].note
    assert got["rrn_noRunJson"].usable and got["rrn_noRunJson"].brief_id == "ses_Y"
    assert not got["rrn_badResult"].usable and "cannot be read" in got["rrn_badResult"].note


def test_research_with_no_recorded_hash_has_an_empty_input_hash(make_run: Any) -> None:
    make_run("rrn_old", export_hash=None)
    (run,) = discover_research(make_run.runs_dir)
    assert run.input_hash == ""


@pytest.mark.parametrize("status", ["complete", "partial", "skipped"])
def test_finished_runs_with_a_result_are_usable(make_run: Any, status: str) -> None:
    make_run("rrn_s", status=status)
    assert discover_research(make_run.runs_dir)[0].usable


@pytest.mark.parametrize("status", ["failed", "running", "queued", "interrupted"])
def test_unfinished_or_failed_runs_are_not_usable(make_run: Any, status: str) -> None:
    make_run("rrn_s", status=status)
    assert not discover_research(make_run.runs_dir)[0].usable


def test_the_local_fallback_gives_the_same_profiles_when_derive_is_absent(
    nine_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "mde_spec_agent.derive.profile", None)
    found = discover_compass([nine_dir])
    assert {e.profile_source for e in found} == {"fallback"}
    assert {e.id: e.profile for e in found} == {k: v[4] for k, v in TABLE.items()}
