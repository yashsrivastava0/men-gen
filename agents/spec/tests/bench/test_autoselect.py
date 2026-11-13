"""Auto-select: matching a Research run to an export, and proposing pairs (doc 19 4.3). Pure function, so no files."""

from __future__ import annotations

import builtins
import os
from pathlib import Path
from typing import Any

import pytest

from mde_spec_agent.bench.autoselect import (
    NO_RESEARCH,
    SYNTH_NO_RESEARCH,
    auto_select,
    best_research,
    matches,
)
from mde_spec_agent.bench.inputs import CompassEntry, discover_compass, discover_research


@pytest.fixture
def nine(nine_dir: Path) -> list[CompassEntry]:
    return discover_compass([nine_dir])


def entry_of(entries: list[CompassEntry], id_: str) -> CompassEntry:
    return next(e for e in entries if e.id == id_)


def test_a_complete_newer_run_is_preferred_over_a_partial_one(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_old_complete", export_hash=yoga.export_hash, created_at="2026-11-11T10:00:00Z")
    make_run(
        "rrn_new_partial",
        status="partial",
        export_hash=yoga.export_hash,
        created_at="2026-11-13T10:00:00Z",
    )
    make_run("rrn_new_complete", export_hash=yoga.export_hash, created_at="2026-11-12T10:00:00Z")
    run, gaps = best_research(yoga, discover_research(make_run.runs_dir))
    assert run is not None and run.id == "rrn_new_complete" and gaps == ()


def test_a_partial_run_is_used_when_no_complete_one_exists_and_the_gap_says_so(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run(
        "rrn_p1", status="partial", export_hash=yoga.export_hash, created_at="2026-11-11T10:00:00Z"
    )
    make_run(
        "rrn_p2", status="partial", export_hash=yoga.export_hash, created_at="2026-11-12T10:00:00Z"
    )
    run, gaps = best_research(yoga, discover_research(make_run.runs_dir))
    assert run is not None and run.id == "rrn_p2"
    assert len(gaps) == 1 and "partial" in gaps[0]


def test_an_older_complete_run_beats_a_newer_failed_one(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_ok", export_hash=yoga.export_hash, created_at="2026-11-10T10:00:00Z")
    make_run(
        "rrn_failed",
        status="failed",
        export_hash=yoga.export_hash,
        created_at="2026-11-13T10:00:00Z",
        research=False,
    )
    run, gaps = best_research(yoga, discover_research(make_run.runs_dir))
    assert run is not None and run.id == "rrn_ok" and gaps == ()


def test_a_complete_run_without_its_research_file_is_not_usable(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_nofile", export_hash=yoga.export_hash, research=False)
    run, gaps = best_research(yoga, discover_research(make_run.runs_dir))
    assert run is None and "left nothing usable" in gaps[0] and "thin research lane" in gaps[0]


def test_only_failed_runs_give_the_unusable_gap(nine: list[CompassEntry], make_run: Any) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_f", status="failed", export_hash=yoga.export_hash)
    run, gaps = best_research(yoga, discover_research(make_run.runs_dir))
    assert run is None and "failed" in gaps[0]


def test_no_match_gives_the_gap_text(nine: list[CompassEntry], make_run: Any) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run(
        "rrn_other",
        export_hash="not-the-yoga-hash",
        brief_id=yoga.brief_id,
        brief_hash=yoga.brief_hash,
    )
    # a recorded hash that differs is a different export even when the Brief ids agree
    assert best_research(yoga, discover_research(make_run.runs_dir)) == (None, (NO_RESEARCH,))
    assert NO_RESEARCH == "no Research run: Spec runs with a thin research lane and says so"
    assert best_research(yoga, []) == (None, (NO_RESEARCH,))


def test_a_run_with_no_recorded_hash_matches_by_brief_id_and_hash(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_nohash", export_hash=None, brief_id=yoga.brief_id, brief_hash=yoga.brief_hash)
    make_run("rrn_wrong_hash", export_hash=None, brief_id=yoga.brief_id, brief_hash="different")
    make_run("rrn_wrong_id", export_hash=None, brief_id="ses_other", brief_hash=yoga.brief_hash)
    runs = discover_research(make_run.runs_dir)
    assert [r.id for r in runs if matches(yoga, r)] == ["rrn_nohash"]
    run, _ = best_research(yoga, runs)
    assert run is not None and run.id == "rrn_nohash"


def test_a_run_with_neither_hash_nor_ids_never_matches(
    nine: list[CompassEntry], make_run: Any
) -> None:
    make_run("rrn_blank", export_hash=None, brief_id="", brief_hash="")
    (run,) = discover_research(make_run.runs_dir)
    assert not any(matches(e, run) for e in nine)


# --- proposals -------------------------------------------------------------------------------------------------------


def test_the_proposals_on_the_nine_exports_without_research(nine: list[CompassEntry]) -> None:
    got = auto_select(nine, [])
    table = [(p.size, p.compass_id, p.synthetic, p.profile, p.research_id) for p in got]
    assert table == [
        ("compact", "quick-yoga-studio-bengaluru", False, "compact", None),
        ("standard", "standard-produce-marketplace-nashik", False, "standard", None),
        ("deep", "thorough-freight-fleet-india", False, "deep", None),
        ("deep", "synth:big", True, "deep", None),
        ("program", "synth:program", True, "deep", None),
    ]
    for p in got:
        assert p.reason and p.gaps
    assert got[0].gaps == (NO_RESEARCH,)
    assert got[3].gaps == got[4].gaps == (SYNTH_NO_RESEARCH,)


def test_the_proposals_carry_the_best_research_run_for_each(
    nine: list[CompassEntry], make_run: Any
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    produce = entry_of(nine, "standard-produce-marketplace-nashik")
    freight = entry_of(nine, "thorough-freight-fleet-india")
    make_run("rrn_yoga", export_hash=yoga.export_hash)
    make_run("rrn_produce_old", export_hash=produce.export_hash, created_at="2026-11-10T00:00:00Z")
    make_run("rrn_produce_new", export_hash=produce.export_hash, created_at="2026-11-12T00:00:00Z")
    make_run("rrn_freight", status="partial", export_hash=freight.export_hash)
    got = {p.compass_id: p for p in auto_select(nine, discover_research(make_run.runs_dir))}
    assert got[yoga.id].research_id == "rrn_yoga" and got[yoga.id].gaps == ()
    assert got[yoga.id].research_path.endswith("rrn_yoga/research.json")
    assert got[produce.id].research_id == "rrn_produce_new"
    assert got[freight.id].research_id == "rrn_freight" and "partial" in got[freight.id].gaps[0]
    assert "recorded input hash" in got[yoga.id].reason
    assert got["synth:program"].research_id is None


def test_the_smallest_ready_export_of_a_class_is_proposed_real_before_hand_made(
    nine: list[CompassEntry],
) -> None:
    # without the real ones, the hand-made file of the class is proposed (the reason says it is a fixture)
    only_fixtures = [e for e in nine if e.fixture]
    got = {p.compass_id: p for p in auto_select(only_fixtures, [])}
    assert (
        "synthetic-small-bike-repair-porto" in got
        and "a hand-made fixture" in got["synthetic-small-bike-repair-porto"].reason
    )
    # with a drifting real export and a clean hand-made one of the same class, the real one still wins
    delhi, school = (
        entry_of(nine, "delhi-restaurant-delivery"),
        entry_of(nine, "synthetic-medium-school-parents-dakar"),
    )
    (standard,) = auto_select([school, delhi], [], "standard")
    assert standard.compass_id == "delhi-restaurant-delivery"
    assert any("drift" in g for g in standard.gaps)


def test_an_export_that_is_not_ready_is_never_proposed(nine: list[CompassEntry]) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    not_ready = yoga.model_copy(
        update={"readiness": yoga.readiness.model_copy(update={"ready": False, "missing": ("x",)})}
    )
    got = auto_select([not_ready], [])
    assert [p.compass_id for p in got] == ["synth:big", "synth:program"]


@pytest.mark.parametrize(
    ("size", "ids"),
    [
        ("compact", ["quick-yoga-studio-bengaluru"]),
        ("standard", ["standard-produce-marketplace-nashik"]),
        ("deep", ["thorough-freight-fleet-india", "synth:big"]),
        ("program", ["synth:program"]),
    ],
)
def test_size_filters_the_proposals(nine: list[CompassEntry], size: str, ids: list[str]) -> None:
    assert [p.compass_id for p in auto_select(nine, [], size)] == ids


def test_an_unknown_size_is_refused(nine: list[CompassEntry]) -> None:
    with pytest.raises(ValueError, match="size must be one of"):
        auto_select(nine, [], "huge")


def test_auto_select_does_no_io(
    nine: list[CompassEntry], make_run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    yoga = entry_of(nine, "quick-yoga-studio-bengaluru")
    make_run("rrn_yoga", export_hash=yoga.export_hash)
    research = discover_research(make_run.runs_dir)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("auto_select touched the disk")

    for target in (builtins, os):
        for name in ("open", "stat", "listdir", "scandir"):
            if hasattr(target, name):
                monkeypatch.setattr(target, name, boom)
    for name in ("open", "read_text", "read_bytes", "stat", "iterdir", "glob", "exists", "is_file"):
        monkeypatch.setattr(Path, name, boom)
    assert [p.compass_id for p in auto_select(nine, research)][:1] == [
        "quick-yoga-studio-bengaluru"
    ]


def test_auto_select_does_not_change_its_inputs(nine: list[CompassEntry], make_run: Any) -> None:
    make_run("rrn_a", export_hash=nine[0].export_hash)
    research = discover_research(make_run.runs_dir)
    before = (list(nine), list(research))
    auto_select(nine, research)
    assert (nine, research) == before
