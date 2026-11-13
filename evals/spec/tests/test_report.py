"""The one report: verdicts, thresholds, money, plain words; nothing is called settled by the tool."""

from __future__ import annotations

import json

import pytest

from evals.spec import report as rp
from mde_spec_core.config import default_config


@pytest.fixture(scope="module")
def run(tmp_path_factory, quick, standard, small_graph):
    out = tmp_path_factory.mktemp("proof")
    rep = rp.run_all({"quick": quick, "standard": standard}, out, None, planted_graph=small_graph)
    return rep, out


def test_every_gate_is_reported_with_numbers_against_the_threshold(run) -> None:
    rep, out = run
    assert set(rep["gates"]) == {
        "PS-4",
        "PS-6",
        "PS-7",
        "PS-10.lookup",
        "PS-10.ablation",
        "PS-10.diversity",
    }
    for g in rep["gates"].values():
        assert g["verdict"] in ("pass", "fail", "not_measured", "single_run")
    t = default_config().proof
    assert (
        rep["thresholds"]["blocking_questions_per_screen_max"]
        == t.blocking_questions_per_screen_max
    )
    ps6_checks = rep["gates"]["PS-6"]["checks"]
    assert any(c["threshold"] == t.acceptance_satisfiable_min for c in ps6_checks)
    assert json.loads((out / "proof-report.json").read_text())["schema"] == "spec.proof-report.v1"


def test_offline_stub_results_are_not_measured(run) -> None:
    rep, _ = run
    assert rep["mode"] == "offline_stub"
    assert set(rep["verdicts"].values()) == {"not_measured"}


def test_money_is_integer_micro_usd_and_zero_offline(run) -> None:
    rep, _ = run
    m = rep["money"]
    assert m["cost_micro_usd"] == 0 and isinstance(m["cost_micro_usd"], int)
    assert (
        m["live_calls"] == 0
        and m["calls"] > 100
        and m["cap_micro_usd"] == default_config().money.cap_micro_usd
    )


def test_the_plain_words_file_is_text_not_markdown_and_never_says_proven(run) -> None:
    _, out = run
    txt = (out / "proof-report.txt").read_text()
    assert "OFFLINE MOCK" in txt and "not measured" in txt
    assert "proven" not in txt.lower() and "# " not in txt and "**" not in txt
    assert "PS-7" in txt and "owner-reading" in txt
    assert not list(out.rglob("*.md"))  # no markdown outside docs/


def test_owner_files_are_written(run) -> None:
    _, out = run
    assert (
        len(
            json.loads(
                (out / "ps-6" / "standard" / "owner-reading" / "probe-outputs.json").read_text()
            )["outputs"]
        )
        == 10
    )
    assert (
        len(
            json.loads((out / "ps-4" / "standard" / "owner-reading" / "cards.json").read_text())[
                "cards"
            ]
        )
        == 5
    )


def test_the_report_is_deterministic(tmp_path_factory, quick) -> None:
    a = tmp_path_factory.mktemp("a")
    b = tmp_path_factory.mktemp("b")
    for d in (a, b):
        rp.run_all({"quick": quick}, d, None)
    assert (a / "proof-report.json").read_text() == (b / "proof-report.json").read_text()


def test_a_pack_folder_is_accepted(tmp_path, quick) -> None:
    from mde_spec_core.lint_pack import write_pack_dir

    write_pack_dir(quick.pack, tmp_path / "pack")
    packs = rp.resolve_packs(tmp_path / "pack")
    assert list(packs) == ["quick"] and packs["quick"].graph is None
    rep = rp.run_all(tmp_path / "pack", tmp_path / "out")
    assert rep["gates"]["PS-7"]["verdict"] == "not_measured" and rep["gates"]["PS-7"]["skipped"]
    assert rep["gates"]["PS-10.diversity"]["skipped"]  # one pack only
    assert rep["gates"]["PS-6"]["verdict"] == "not_measured"


def test_worst_verdict_ordering() -> None:
    assert rp._worst(["pass", "fail"]) == "fail"
    assert rp._worst(["pass", "single_run"]) == "single_run"
    assert rp._worst(["pass", "not_measured"]) == "not_measured"
    assert rp._worst(["pass", "pass"]) == "pass"
