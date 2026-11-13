"""Engine side of the planner overhaul (D-163): a named thing with no public footprint leaves the required-slot gate, a
run past its soft time goes on only while it still pays, and a large plan shares the page ceiling between its topics."""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.fakes import FakeWorld
from support.testing import run_engine

from mde_core.clock import FakeClock
from mde_research_agent.catalog import load_catalog
from mde_research_agent.engine.worker import pages_wanted
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan

ROOT = Path(__file__).resolve().parents[4]
DELHI = ROOT / "docs" / "compass" / "research-inputs" / "delhi-restaurant-delivery.json"
CAT = load_catalog()


def delhi() -> dict:
    return json.loads(DELHI.read_text(encoding="utf-8"))


async def test_a_named_reference_with_no_footprint_ends_not_found_and_leaves_the_gate() -> None:
    export = delhi()
    plan = build_plan(read_export(export, CAT), CAT)
    names = [t.entity for t in plan.targets if t.template_id == "references" and t.entity]
    assert names, "the input names apps the person wants researched"
    ghost = names[0]
    clock = FakeClock()
    world = FakeWorld(provider_content_every=3, clock=clock)
    world.route(re.escape(ghost), [])  # the web has nothing about it, whatever the wording
    run = await run_engine(export, world=world, clock=clock)
    ts = next(
        s
        for s in run.ctx.targets.values()
        if s.target.template_id == "references" and s.target.entity == ghost
    )
    assert ts.status == "not_found" and ts.reason == "no_public_footprint" and ts.gate_excluded
    assert len({q.casefold() for q in ts.queries}) >= 2, "two formulations were tried first"
    assert ts.followups == 0, "no gap-analyst follow-up is paid for a thing nobody wrote about"
    assert (ts.slots_required, ts.slots_filled) == (0, 0)
    found = [e for e in run.events if e.type == "target.not_found" and e.target_id == ts.target.id]
    assert found
    # the run's required-slot total does not count it, and it is still reported as a gap
    total = sum(s.slots_required for s in run.ctx.targets.values())
    assert run.research["coverage"]["slots_required"] == total
    gaps = [g for g in run.research["gaps"] if g["target_id"] == ts.target.id]
    assert gaps and gaps[0]["reason"] == "not_found"


async def test_a_reference_that_is_found_still_counts_against_the_gate() -> None:
    run = await run_engine(delhi())
    refs = [s for s in run.ctx.targets.values() if s.target.template_id == "references"]
    assert refs
    for s in refs:
        if s.status != "not_found":
            assert not s.gate_excluded and s.slots_required >= 1


async def test_only_named_reference_templates_can_leave_the_gate() -> None:
    export = delhi()
    clock = FakeClock()
    world = FakeWorld(
        generate=False, clock=clock
    )  # the web is empty: every topic comes back not found or partial
    run = await run_engine(export, world=world, clock=clock, validate=False)
    for s in run.ctx.targets.values():
        if s.gate_excluded:
            assert f"{s.target.job}:{s.target.template_id}" in CAT.plan.named_reference_templates


async def test_past_the_soft_time_a_run_goes_on_only_while_it_still_pays() -> None:
    clock = FakeClock()
    run = await run_engine(
        delhi(), clock=clock, ceilings={"soft": {"seconds": 100}, "hard": {"seconds": 400}}
    )
    d, b = run.director, run.ctx.budget
    floor = CAT.ceilings.min_round_gain
    clock.advance(100 - b.elapsed() + 1)  # past the soft seconds, inside the hard ones
    assert b.time_up_soft() and not b.time_up_hard() and b.exhausted() is None
    d._last_gain = floor - 0.01
    assert d._time_stop(), "the last round paid too little: stop at the soft time"
    d._last_gain = floor
    assert not d._time_stop(), "the last round still paid: go on"
    clock.advance(400)
    assert b.time_up_hard() and b.exhausted() == "time", "the hard time is never exceeded"


async def test_round_one_gain_is_what_a_run_past_its_soft_time_is_judged_by() -> None:
    clock = FakeClock()
    world = FakeWorld(provider_content_every=3, clock=clock, search_seconds=3.0)
    run = await run_engine(
        delhi(),
        world=world,
        clock=clock,
        ceilings={"soft": {"seconds": 20}, "hard": {"seconds": 5000}},
    )
    assert run.ctx.budget.time_up_soft()
    # slow searches pushed the run past its soft time during round one; it still reached a later round because round
    # one filled far more than min_round_gain of the required slots
    assert run.result.rounds >= 2 or not [
        s for s in run.ctx.targets.values() if s.status == "partial"
    ]


async def test_a_large_plan_shares_the_page_ceiling_between_its_topics() -> None:
    run = await run_engine(delhi())
    ctx = run.ctx
    cap = CAT.ceilings.pages_per_topic
    assert pages_wanted(ctx, 2) == min(cap.max, 3)
    n = len(ctx.targets)
    want = pages_wanted(ctx, 1)
    assert cap.min <= want <= cap.max
    # never more than three quarters of the hard page ceiling is promised to round one (unless the minimum forces it)
    assert want * n <= max(ctx.budget.hard.pages * 3 // 4, cap.min * n)
    # a small plan keeps the usual number
    ctx.targets = dict(list(ctx.targets.items())[:3])
    assert pages_wanted(ctx, 1) == cap.max
