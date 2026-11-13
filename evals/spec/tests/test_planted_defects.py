"""PS-7: 22 structural + 8 meaning defects against the self-check, a proof-only critic arm and Verify-for-deep (offline)."""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.spec import planted_defects as pd
from evals.spec import planters
from evals.spec.common import make_proof_runner
from evals.spec.planters import Note
from mde_spec_core import planted
from mde_spec_core.config import default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.pack import Pack, assemble


@pytest.fixture(scope="module")
def small_report(tmp_path_factory, small_graph):
    runner = make_proof_runner(tmp_path_factory.mktemp("ps7"))
    rep = asyncio.run(pd.run_planted_defects(runner, small_graph, name="synthetic-small"))
    return rep, runner


def test_self_check_catches_all_22_structural_kinds_via_the_leads_runner(
    small_report, small_graph
) -> None:
    rep, _ = small_report
    s = rep.stats("structural", "self_check")
    assert (
        s["planted"] == 22
        and s["caught"] == 22
        and s["overall"] == 1.0
        and s["min_per_kind"] == 1.0
    )
    base = planters.prepare_baseline(small_graph)
    direct = planted.run_planted(base)
    assert len(direct) == 22 and all(r.caught for r in direct)
    assert set(s["per_kind"]) == {d.id for d in planted.STRUCTURAL}


def test_meaning_kinds_are_reported_separately_without_a_bar(small_report) -> None:
    rep, _ = small_report
    d = rep.as_dict()
    for arm in rep.arms:
        m = d["arms"][arm]["meaning"]
        assert m["bar"].startswith("none")
        assert set(m["per_kind"]) <= {x.id for x in planted.MEANING}
        # no check of the gate mentions a meaning kind: they carry no threshold
        assert not [c for c in d["arms"][arm]["checks"] if "meaning" in c["name"]]
    assert d["structural_kinds"] == 22 and d["meaning_kinds"] == 8
    # the self-check is code: it cannot judge meaning, so most meaning kinds escape it; that is what the test shows
    sm = rep.stats("meaning", "self_check")
    assert 0 < sm["caught"] < sm["planted"] and sm["missed"]


def test_false_positives_on_the_unmodified_pack_are_measured(small_report) -> None:
    rep, _ = small_report
    fp = rep.false_positives
    assert set(fp) == {"self_check", "critic", "verify"}
    assert all(0.0 <= v["rate"] <= 1.0 for v in fp.values())
    assert fp["self_check"]["rate"] <= default_config().proof.false_positive_max


def test_the_critic_arms_and_rounds_and_vendor(small_report) -> None:
    rep, runner = small_report
    d = rep.as_dict()
    assert rep.rounds_used_max <= default_config().verify.max_llm_rounds == 2
    assert d["critic_vendor"] == "same"
    assert d["canary_scope"] == "changed"
    # offline the critic is the mock critic: the gate is not a measurement and says so
    assert d["verdict"] == "not_measured" and "MOCK critic" in d["note"]
    assert d["arms"]["self_check"]["verdict"] == "pass"  # code is a real measurement
    assert (
        d["arms"]["critic"]["verdict"] == "not_measured"
        and d["arms"]["verify"]["verdict"] == "not_measured"
    )
    assert d["money"]["cost_micro_usd"] == 0 and d["money"]["calls"] > 0
    # every call went through the guarded runner
    assert len(runner.calls) == d["money"]["calls"]


def test_the_mock_critic_catches_what_its_mock_rules_catch(small_report) -> None:
    rep, _ = small_report
    rows = {r["defect"]: r for r in rep.rows}
    # the Brief's Locked number was changed: a code reading of the Brief's Locked facts sees it
    assert (
        rows["changed_locked_number"]["critic"]["caught"]
        and rows["changed_locked_number"]["verify"]["caught"]
    )
    # a story is not part of what Verify's digest shows: no critic could see it, and the report says so per kind
    assert rows["story_contradicts_a_rule"]["critic_input_differs"] is False
    assert not rows["story_contradicts_a_rule"]["critic"]["caught"]
    assert rows["changed_locked_number"]["critic_input_differs"] is True


# ------------------------------------------------------------------------------------------ the meaning planters


class Planted:
    """A plant with the optional parts proven present."""

    def __init__(self, case: pd.Case) -> None:
        assert case.applicable, case.reason
        assert case.graph is not None and case.pack is not None and case.note is not None
        self.graph: SpecGraph = case.graph
        self.pack: Pack = case.pack
        self.note: Note = case.note
        self.ctx_kwargs = case.ctx_kwargs


def _plant(base, d) -> Planted:
    return Planted(pd.plant(base, d))


@pytest.mark.parametrize("kind", list(planters.MEANING_PLANTERS))
def test_every_real_meaning_planter_changes_the_pack(kind, small_graph) -> None:
    base = planters.prepare_baseline(small_graph)
    clean_pack = assemble(base)
    d = next(x for x in planters.real_meaning_defects() if x.id == kind)
    case = _plant(base, d)
    assert (
        case.graph.content_hash() != base.content_hash()
    )  # the pack content differs from the clean one
    assert case.note.touched
    if planters.PACK_VISIBLE[kind]:
        assert case.pack.package_hash != clean_pack.package_hash
        changed = {p for p in case.pack.files if case.pack.files[p] != clean_pack.files.get(p)}
        assert changed - {"MANIFEST.json", "index.json", "build/build-plan.json"}, (
            "a rendered file changed"
        )


def test_meaning_planters_use_the_same_ids_as_the_leads_catalogue() -> None:
    assert [d.id for d in planters.real_meaning_defects()] == [d.id for d in planted.MEANING]
    assert all(d.kind == "meaning" for d in planters.real_meaning_defects())


def test_specific_planters_do_what_doc_18_describes(small_graph) -> None:
    base = planters.prepare_baseline(small_graph)
    rule = base.rules["RUL-001"]
    assert rule.tier == "locked" and rule.value == "4"  # the Brief's number
    by = {d.id: _plant(base, d) for d in planters.real_meaning_defects()}
    changed = by["changed_locked_number"].graph.rules["RUL-001"]
    assert (changed.value, "5" in changed.statement or "4" not in changed.statement) == ("5", True)
    g2 = by["two_contradictory_rules"].graph
    new = next(r for r in g2.rules.values() if r.id != "RUL-001")
    assert new.subject == rule.subject and new.value == "0"
    assert not any(
        planters.PAYMENT_WORDS.search(u)
        for f in by["journey_without_payment_failure_path"].graph.flows.values()
        for u in f.unhappy_paths
    )
    assert any(
        planters.PAYMENT_WORDS.search(u) for f in base.flows.values() for u in f.unhappy_paths
    )
    forbidden = by["role_sees_what_brief_forbids"].graph
    assert any(r.actions == ["manage"] for e in forbidden.entities.values() for r in e.access)
    story = next(iter(by["story_contradicts_a_rule"].graph.stories.values()))
    assert "never" in story.want
    s = by["assumed_item_shown_as_locked"].graph.screens[
        by["assumed_item_shown_as_locked"].note.touched[0]
    ]
    assert (s.assumed, s.tier) == (True, "locked")
    res = by["default_contradicts_official_research"].ctx_kwargs["research_items"]
    assert res[0]["evidence"] == "official"
    gone = by["user_feature_missing_from_inventory"].graph
    assert len(gone.screens) < len(base.screens)


def test_brief_comes_from_the_clean_graph(small_graph) -> None:
    base = planters.prepare_baseline(small_graph)
    b = planters.brief_for(base)
    assert [f.node for f in b.locked_facts] == ["RUL-001"] and b.locked_facts[0].value == "4"
    assert any("may" in q for q in b.quotes)


# ------------------------------------------------------------------------------------------ a real baseline pack


def test_real_quick_pack_self_check_is_honest_about_what_it_cannot_plant(quick, tmp_path) -> None:
    runner = make_proof_runner(tmp_path)
    rep = asyncio.run(
        pd.run_planted_defects(runner, quick.graph, name="quick", arms=("self_check",))
    )
    d = rep.as_dict()
    na = {x["defect"] for x in d["not_applicable"]}
    assert na == {
        "phase1_depends_on_phase2",
        "later_feature_has_screen_node",
    }  # their ids collide with the real graph
    s = d["arms"]["self_check"]["structural"]
    assert s["planted"] == 20 and s["caught"] >= 18
    # a plant whose validator already reports on the unmodified real pack counts as missed, and it is named
    assert set(d["clean_pack"]["masked_kinds"]) == set(s["missed"])
    assert d["clean_pack"]["findings"] > 0
    assert d["verdict"] in ("pass", "fail")  # code only: a real measurement
    assert d["money"]["calls"] == 0


def test_report_is_written(small_report, tmp_path) -> None:
    rep, _ = small_report
    path = pd.write_report(rep, tmp_path)
    j = json.loads(path.read_text())
    assert j["gate"] == "PS-7" and len(j["rows"]) == 30
    assert all(isinstance(j["money"]["cost_micro_usd"], int) for _ in [0])
