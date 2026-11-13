"""PS-10 diversity (between apps) and consistency (within one), COULD off and on, measured separately."""

from __future__ import annotations

import json

from evals.spec import diversity as dv
from evals.spec import packs
from evals.spec.synthetic import attach_could, attach_directions
from mde_spec_core.synth import make_graph


def _pack(name: str, seed: int, could_seed: int):
    g = attach_could(attach_directions(make_graph("small"), seed=seed), seed=could_seed)
    return packs.from_graph(name, g)


def test_baseline_packs_have_no_direction_and_say_so(quick, standard) -> None:
    r = dv.between_app(dv.signature(quick), dv.signature(standard), could=False)
    assert r == {
        "applicable": False,
        "why": "a pack has no direction (the Design lane has not run)",
    }


async def test_two_apps_from_different_seeds_differ(runner) -> None:
    a, b = _pack("A", 1, 1), _pack("B", 5, 2)
    rep = await dv.run_diversity(a, b, runner)
    off, on = rep.between["could_off"], rep.between["could_on"]
    assert off["applicable"] and off["min_axis_diff"] >= 1
    assert (
        on["could_signature_difference"] is not None and off["could_signature_difference"] is None
    )
    assert 0.0 <= off["diversity"] <= 1.0
    assert rep.critic["asked"] and rep.critic["between_app"]["score"] in range(1, 6)
    assert rep.verdict() == "not_measured" and "NOT written by a live lane" in rep.as_dict()["note"]
    assert rep.tally.micros == 0


def test_same_app_twice_is_not_diverse() -> None:
    a = _pack("A", 1, 1)
    same = dv.between_app(dv.signature(a), dv.signature(a), could=True)
    assert same["min_axis_diff"] == 0 and same["diversity"] < 1.0


def test_within_app_consistency_is_separate_for_could_on_and_off() -> None:
    a = _pack("A", 1, 1)
    off = dv.within_app(a, could=False)
    on = dv.within_app(a, could=True)
    assert (
        off["applicable"]
        and on["motion_consistency"] is not None
        and off["motion_consistency"] is None
    )
    assert (
        off["shape_consistency"] == on["shape_consistency"]
    )  # MUST content is the same either way
    assert 0.0 <= off["consistency"] <= 1.0 and 0.0 <= on["consistency"] <= 1.0


def test_signature_from_files_matches_the_graph_for_could(tmp_path) -> None:
    from mde_spec_core.lint_pack import write_pack_dir

    a = _pack("A", 1, 1)
    write_pack_dir(a.pack, tmp_path / "pack")
    from_files = dv.signature(packs.from_dir(tmp_path / "pack"))
    from_graph = dv.signature(a)
    assert sorted(from_files.could) == sorted(from_graph.could)
    assert from_files.source == "files" and from_graph.source == "graph"


def test_circular_gap() -> None:
    assert dv.circular_gap(350, 10) == 20 and dv.circular_gap(0, 180) == 180


async def test_report_writes(tmp_path, runner) -> None:
    rep = await dv.run_diversity(_pack("A", 1, 1), _pack("B", 5, 2), runner)
    path = dv.write_report(rep, tmp_path)
    assert json.loads(path.read_text())["gate"] == "PS-10.diversity"
