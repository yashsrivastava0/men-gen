"""PS-6: the consumer probe on real baseline packs, offline (the mock reader through the real gateway pipeline)."""

from __future__ import annotations

import json

from evals.spec import consumer_probe as cp
from evals.spec import packs
from evals.spec.common import spent_micros
from mde_spec_core.config import default_config


def test_sample_sizes_and_types_are_chosen_by_code(quick, standard, thorough) -> None:
    q, s, t = (
        packs.pick_samples(quick, "quick"),
        packs.pick_samples(standard, "standard"),
        packs.pick_samples(thorough, "thorough"),
    )
    assert (len(q), len(s), len(t)) == (3, 6, 9)
    assert len(set(t)) == 9
    types = {
        k: {packs.cards_of(p)[sid].screen_type for sid in ids}
        for k, p, ids in (("q", quick, q), ("s", standard, s))
    }
    assert {"form", "checkout"} <= types["s"] or len(
        types["s"]
    ) >= 5  # the real export may lack a type: filled by the next
    assert packs.pick_samples(standard, "standard") == s  # deterministic
    cards = packs.cards_of(thorough)

    def cross(c) -> bool:
        return any(cards[x].app != c.app for x in c.leads_to if x in cards)

    if any(
        cross(c) for c in cards.values()
    ):  # a cross-app step is in the Thorough sample when the export has one
        assert any(cross(cards[x]) for x in t)
    others = [
        c.weight
        for sid, c in cards.items()
        if sid not in t and c.screen_type not in ("auth", "legal", "permission", "onboarding")
    ]
    assert max(cards[x].weight for x in t) >= max(others, default=0)  # the heavy screen is in


def test_arms_on_quick_and_standard(runner, quick, standard) -> None:
    for pack, size in ((quick, "quick"), (standard, "standard")):
        rep = _run(runner, pack, size)
        arms = rep.arms
        assert arms["A"].probes and arms["C"].probes
        assert (
            arms["B"].skipped and "does not fit" in arms["B"].skipped
        )  # whole pack > whole_pack_tokens
        assert rep.comparisons()["slice_not_worse_than_whole"] is None
        assert (
            rep.comparisons()["card_alone_clearly_worse"] is True
        )  # the card without the global layer asks for it
        m = rep.arm_metrics()
        assert m["A"]["blocking_per_screen"] == 0.0 and m["C"]["blocking_per_screen"] > 0.3
        assert len(rep.screens) == packs.SAMPLE_SIZE[size]


def _run(runner, pack, size):
    import asyncio

    return asyncio.run(cp.run_consumer_probe(runner, pack, size))


async def test_whole_pack_arm_runs_where_it_fits(runner, tiny) -> None:
    assert tiny.total_tokens <= tiny.manifest["whole_pack_tokens"]
    rep = await cp.run_consumer_probe(runner, tiny, "quick")
    assert rep.arms["B"].probes and not rep.arms["B"].skipped
    cmp_ = rep.comparisons()
    assert cmp_["slice_not_worse_than_whole"] is True
    # arm B reads every file of the pack; arm A only the card's reads; arm C only the card
    sid = rep.screens[0]
    assert (
        len(rep.arms["B"].probes[0].reads)
        > len(rep.arms["A"].probes[0].reads)
        > len(rep.arms["C"].probes[0].reads)
        == 1
    )
    assert rep.arms["C"].probes[0].reads == [f"screens/{sid}.md"]


async def test_report_compares_against_config_and_is_not_measured_offline(
    runner, standard, tmp_path
) -> None:
    rep = await cp.run_consumer_probe(runner, standard, "standard")
    t = default_config().proof
    by_name = {c.name: c for c in rep.checks()}
    assert (
        by_name["A blocking questions per screen"].threshold == t.blocking_questions_per_screen_max
    )
    assert by_name["A assumptions per screen"].threshold == t.assumptions_per_screen_max
    assert by_name["A acceptance satisfiable share"].threshold == t.acceptance_satisfiable_min
    assert (
        rep.verdict() == "not_measured" and rep.would_pass() is True
    )  # the stub proves the harness only
    d = rep.as_dict()
    assert d["single_run"] is True and "OFFLINE STUB" in d["note"]
    path = cp.write_report(rep, tmp_path)
    assert json.loads(path.read_text())["gate"] == "PS-6"
    owner = json.loads((tmp_path / "owner-reading" / "probe-outputs.json").read_text())
    assert len(owner["outputs"]) == 10 and owner["offline_stub"] is True
    assert {o["arm"] for o in owner["outputs"]} == {"A", "C"}
    assert all(
        {"outline", "blocking_questions", "assumptions", "acceptance_coverage"} <= set(o)
        for o in owner["outputs"]
    )
    assert rep.tally.micros == 0 and isinstance(rep.tally.micros, int) and spent_micros(runner) == 0


async def test_a_live_single_run_is_reported_as_single_run() -> None:
    from evals.spec.common import verdict_for

    assert verdict_for(True, live=True, runs=1) == "single_run"
    assert verdict_for(True, live=True, runs=3) == "pass"
    assert verdict_for(False, live=True, runs=3) == "fail"
    assert verdict_for(True, live=False, runs=3) == "not_measured"
    assert verdict_for(None, live=True, runs=3) == "not_measured"


async def test_developer_test_turns_gaps_into_required_items_and_rejects_layout(
    runner, standard
) -> None:
    sid = "SCR-014"
    assert sid in standard.screen_ids()
    res = await cp.developer_test(runner, standard, sid)
    assert res["screen"] == sid and res["questions"]  # arm C (card alone) records its gaps
    kinds = {q["kind"] for q in res["questions"]}
    assert kinds <= {"behaviors_category", "rules_entry", "needs_review", "rejected_layout"}
    # classification rules
    assert (
        cp.classify_gap("What happens when the app is killed during the redirect?")["category"]
        == "interrupted_external_flow"
    )
    assert cp.classify_gap("What is the maximum fee in percent?")["kind"] == "rules_entry"
    assert (
        cp.classify_gap("Where should the button sit at the top of the screen?")["kind"]
        == "rejected_layout"
    )


def test_gap_class_categories_are_real_behavior_categories() -> None:
    from mde_spec_core.vocab import behavior_categories

    gc = cp._gap_classes()
    assert set(gc["behaviors"]) <= set(behavior_categories())
