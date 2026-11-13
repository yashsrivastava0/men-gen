"""PS-10 library lookup: the harness, the fixtures (two dated public catalogues) and the synthetic gap catalogue."""

from __future__ import annotations

import json

import pytest

from evals.spec import catalogues as cat
from evals.spec import lookup_probe as lp
from evals.spec.common import FIXTURES_DIR
from mde_spec_core import vocab


@pytest.mark.parametrize("key", cat.REAL_LIBRARIES)
def test_catalogue_fixtures_load_and_every_component_has_a_purpose_and_source(key) -> None:
    c = cat.load_catalogue(key)
    raw = json.loads((FIXTURES_DIR / "catalogues" / f"{key}.json").read_text())
    assert {"library", "source_url", "checked_on", "components"} <= set(raw)
    assert raw["checked_on"] == "2026-11-14" and raw["source_url"].startswith("https://")
    assert len(c.components) >= 20
    names = [x.name for x in c.components]
    assert len(names) == len(set(names))
    for comp in c.components:
        assert comp.purpose.strip() and comp.source_url.startswith("https://"), comp.name
        assert comp.confidence in ("high", "medium", "low")
        assert set(comp.shapes) <= set(vocab.shapes()), (comp.name, comp.shapes)
    assert any(x.shapes for x in c.components)
    assert c.excluded  # what was not read is said out loud


def test_two_real_libraries_and_their_known_gaps() -> None:
    libs = cat.real_catalogues()
    assert [c.key for c in libs] == ["react-native-paper", "react-native-elements"]
    for c in libs:
        assert {"places", "conversation", "schedule"} <= set(
            c.gap_shapes()
        )  # neither library has these as a component


def test_synthetic_catalogue_is_about_25_components_with_planted_gaps() -> None:
    s = cat.synthetic_catalogue()
    assert 22 <= len(s.components) <= 28
    assert s.planted_gaps and set(s.gap_shapes()) >= set(s.planted_gaps)
    for shape in s.planted_gaps:
        assert not s.fitting(shape)


def test_held_out_split_is_deterministic_and_disjoint(standard) -> None:
    needs = lp.load_needs(standard)
    assert len(needs) >= 10
    tune, held = lp.split_needs(needs)
    assert lp.split_needs(needs) == (tune, held)
    assert not {n["id"] for n in tune} & {n["id"] for n in held}
    assert {n["id"] for n in tune} | {n["id"] for n in held} == {n["id"] for n in needs}
    assert held and tune


async def test_synthetic_planted_gaps_are_found_by_the_stub(runner, standard) -> None:
    rep = await lp.run_lookup_probe(runner, standard)
    syn = rep.synthetic
    assert syn is not None
    assert syn.planted["gap_found_rate"] == 1.0 and syn.planted["gaps_found"] == len(
        syn.planted["gap_shapes"]
    )
    assert syn.planted["wrong_gap_rate"] == 0.0


async def test_lookup_metrics_and_baseline(runner, standard, tmp_path) -> None:
    rep = await lp.run_lookup_probe(runner, standard)
    assert [r.key for r in rep.libraries] == ["react-native-paper", "react-native-elements"]
    for r in rep.libraries:
        n, b = r.arms["needs"], r.arms["card_only"]
        assert (
            n["recall_at_3"] == 1.0 and n["wrong_gap_rate"] == 0.0
        )  # the stub matches shape names
        assert (
            b["recall_at_3"] < n["recall_at_3"]
        )  # without the needs inventory the baseline is worse
        assert n["fit"] > 0 and n["gap"] > 0
    assert rep.verdict() == "not_measured" and rep.would_pass() is True
    names = {c.name for c in rep.checks()}
    assert "react-native-paper: recall at 3" in names and "synthetic: planted gaps found" in names
    lp.write_report(rep, tmp_path)
    d = json.loads((tmp_path / "lookup-probe.json").read_text())
    assert d["libraries"][0]["checked_on"] == "2026-11-14" and "OFFLINE STUB" in d["note"]


def test_scoring_counts_wrong_gaps_and_misses() -> None:
    from evals.spec.calls.lookup.schema import LookupOut, MatchOut

    c = cat.load_catalogue("react-native-paper")
    needs = [
        {"id": "N1", "shape": "collection"},
        {"id": "N2", "shape": "collection"},
        {"id": "N3", "shape": "places"},
        {"id": "N4", "shape": "places"},
    ]
    out = LookupOut(
        matches=[
            MatchOut(item="N1", candidates=["DataTable"], gap=False, gap_note=""),
            MatchOut(item="N2", candidates=[], gap=True, gap_note="x"),  # wrong gap
            MatchOut(item="N3", candidates=[], gap=True, gap_note="x"),  # planted gap found
            MatchOut(item="N4", candidates=["Card"], gap=False, gap_note=""),  # gap missed
        ]
    )
    s = lp.score(c, needs, out)
    assert (s["recall_at_3"], s["wrong_gap_rate"], s["gap_found_rate"]) == (0.5, 0.5, 0.5)
