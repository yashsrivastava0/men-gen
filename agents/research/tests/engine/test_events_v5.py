"""RC-6: events and snapshot additions (slots_required, stable fact ids, a facts list, stage timings)."""

from __future__ import annotations

from support.testing import load_fixture, run_engine

from mde_research_agent.events.reducer import fold


async def test_plan_ready_carries_the_slots_required_per_topic_and_in_total() -> None:
    run = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    plan = next(e for e in run.events if e.type == "plan.ready")
    per = [t["slots_required"] for t in plan.detail["targets"]]
    assert all(isinstance(n, int) for n in per) and plan.detail["slots_required"] == sum(per) > 0
    view = fold(run.events)
    assert view["slots_required"] == sum(per) and [t.slots_required for t in view["targets"]] == per


async def test_a_finding_keeps_one_id_from_checking_to_checked_and_the_snapshot_lists_it() -> None:
    run = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    found = [e for e in run.events if e.type == "fact.found"]
    done = [e for e in run.events if e.type in ("fact.verified", "fact.rejected")]
    assert found and all(e.detail.get("fact_id") for e in found + done)
    ids_found = {e.detail["fact_id"] for e in found}
    assert {e.detail["fact_id"] for e in done if e.type == "fact.verified"} <= ids_found
    facts = fold(run.events)["facts"]
    assert {f.fact_id for f in facts} >= ids_found
    assert all(
        f.state in ("checked", "rejected")
        for f in facts
        if f.fact_id in {e.detail["fact_id"] for e in done}
    )
    verified_atoms = {a["id"] for a in run.evidence["atoms"] if a["status"] == "verified"}
    assert verified_atoms <= {
        f.fact_id for f in facts if f.state == "checked"
    }  # the id is the atom's id


async def test_the_run_block_records_stage_timings_models_cost_by_step_and_fallbacks() -> None:
    run = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    info = run.research["run"]
    assert {"plan", "extract", "check"} <= set(info["stages"]) or run.ctx.timings
    assert isinstance(info["cost_by_step"], dict) and isinstance(info["fallbacks"], dict)
    assert info["writer_model"] and info["checker_model"]
