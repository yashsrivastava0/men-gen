"""The re-extraction harness: same pages, shared extraction, arms that differ only in the checker (D-159)."""

from __future__ import annotations

import json

from support.testing import load_fixture, run_engine

from mde_research_agent.calls.base import CallRunner
from mde_research_agent.eval.reextract import ARMS, pairs_of, run_reextract
from mde_research_agent.ids import CounterIds

STANDARD = "standard-produce-marketplace-nashik"


async def test_arms_share_one_extraction_and_report_ranges_by_arm() -> None:
    run = await run_engine(load_fixture(STANDARD))
    runner = CallRunner(run.gateway, run_id="eval", ids=CounterIds())  # type: ignore[arg-type]
    rep = await run_reextract(
        runner,
        run.evidence,
        run.ctx.input.model_dump(mode="json"),
        arms=["A", "A3", "B"],
        repeats=2,
    )
    assert rep["pairs"] > 0 and rep["repeats"] == 2 and set(rep["arms"]) == {"A", "A3", "B"}
    for name, arm in rep["arms"].items():
        assert len(arm["repeats"]) == 2 and arm["what"] == ARMS[name].name
        assert (
            arm["partial_share"]["min"] is None
            or arm["partial_share"]["min"] <= arm["partial_share"]["max"]
        )
    # the extraction is shared, so the arms see the same number of code-verified atoms
    verified = {name: [r["atoms"] for r in arm["repeats"]] for name, arm in rep["arms"].items()}
    assert verified["A"] == verified["A3"] == verified["B"]
    json.dumps(rep)  # the report is plain JSON


async def test_pairs_come_from_the_stored_atoms_and_unmatched_topics_are_counted() -> None:
    run = await run_engine(load_fixture(STANDARD))
    from mde_research_agent.catalog import load_catalog
    from mde_research_agent.plan.build import build_plan

    plan = build_plan(run.ctx.input, load_catalog())
    by_label = {t.label: t for t in plan.targets}
    pairs, missing = pairs_of(run.evidence, by_label)
    assert (
        pairs and all(pid for _, pid in pairs) and len(pairs) == len({(t.id, p) for t, p in pairs})
    )
    assert missing >= 0
    _none, all_missing = pairs_of(run.evidence, {})
    assert _none == [] and all_missing == len(run.evidence["atoms"])
