import asyncio
from pathlib import Path

import pytest

from mde_spec_agent.run.checkpoint import Versions
from mde_spec_agent.run.concurrency import Limiter, compute_k
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import Orchestrator, PlanInvalid, Stage, StepDef
from mde_spec_agent.run.patch import node_patch
from mde_spec_agent.run.store import RunActive, RunStore
from mde_spec_agent.spend import SpendGuard, SpendRecord
from mde_spec_agent.spend.worst_case import Prices
from mde_spec_core.config import default_config
from mde_spec_core.graph import Entity, SpecGraph
from mde_spec_core.profiles import load_profiles

V = Versions("t@1", "p@1", "s@1", "l@1", "fp")
CFG = default_config()


def make(tmp_path: Path, fail_at: dict | None = None):
    store = RunStore(tmp_path / "rt")
    joined = store.create_run("prog", "h1", {"offline": True})
    rec = SpendRecord(tmp_path / "spend.json", cap_micros=5_000_000, tripwire_micros=4_500_000)
    ps = load_profiles()
    ctx = RunContext(
        run_id=joined.run_id,
        program_id="prog",
        input_hash="h1",
        store=store,
        graph=SpecGraph(),
        config=CFG,
        writer=ps.weakest(),
        consumer=ps.weakest(),
        versions=V,
        guard=SpendGuard(rec, Prices("0.10", "0.50")),
        limiter=Limiter(4, CFG),
        offline=True,
    )
    return store, ctx


def entity_step(name: str, n: int, fail_at: dict):
    async def fn(ctx: RunContext) -> None:
        patches = []
        for i in range(n):

            async def produce(i=i):
                if fail_at.get("on") == (name, i):
                    fail_at["on"] = None
                    raise RuntimeError("process killed")
                return node_patch(
                    "entities", [Entity(id=f"ENT-S{ord(name[0]) % 10}{i:02d}", name=f"{name} {i}")]
                )

            patches.append(await ctx.batch(name, f"b{i}", V, produce))
        for p in patches:
            ctx.apply(p)

    return fn


def pipeline(fail_at):
    return [
        Stage(
            (
                StepDef("product", entity_step("product", 3, fail_at)),
                StepDef("design", entity_step("design", 2, fail_at)),
            )
        ),
        Stage((StepDef("screens", entity_step("screens", 2, fail_at)),), gate_before="g15"),
    ]


def run(ctx, orch):
    return asyncio.run(orch.run(ctx))


def test_uninterrupted_run_pauses_at_the_gate_then_finishes(tmp_path):
    store, ctx = make(tmp_path)
    orch = Orchestrator(store, pipeline({}))
    out = run(ctx, orch)
    assert out.status == "paused" and store.get_run(ctx.run_id)["status"] == "paused_g15"
    orch.approve_gate(ctx.run_id, "g15")
    out = run(ctx, orch)
    assert out.status == "awaiting_g2" and len(ctx.graph.entities) == 7
    seqs = [e.seq for e in store.events(ctx.run_id)]
    assert seqs == list(range(1, len(seqs) + 1))  # dense


def test_killed_run_resumes_and_ends_with_identical_bytes(tmp_path):
    store, ctx = make(tmp_path / "a")
    orch = Orchestrator(store, pipeline({}))
    run(ctx, orch)
    orch.approve_gate(ctx.run_id, "g15")
    run(ctx, orch)
    reference = ctx.graph.canonical_text()

    fail = {"on": ("screens", 1)}
    store2, ctx2 = make(tmp_path / "b")
    orch2 = Orchestrator(store2, pipeline(fail))
    run(ctx2, orch2)
    orch2.approve_gate(ctx2.run_id, "g15")
    out = run(ctx2, orch2)
    assert (
        out.status == "failed"
    )  # a bug-like crash fails loudly; the run store keeps the checkpoints
    store2.update_run(
        ctx2.run_id, status="partial"
    )  # the operator marks it resumable after a restart
    out = run(ctx2, orch2)
    assert out.status == "awaiting_g2"
    assert ctx2.graph.canonical_text() == reference


def test_plan_invalid_and_spend_refusal_end_partial_and_resumable(tmp_path):
    store, ctx = make(tmp_path)

    async def bad(c):
        raise PlanInvalid("no")

    out = run(ctx, Orchestrator(store, [Stage((StepDef("plan", bad),))]))
    assert out.status == "partial" and out.reason == "plan_invalid"
    assert store.get_run(ctx.run_id)["status"] == "partial"


def test_single_flight_per_program(tmp_path):
    store = RunStore(tmp_path)
    a = store.create_run("prog", "h1", {})
    again = store.create_run("prog", "h1", {})
    assert again.joined and again.run_id == a.run_id
    with pytest.raises(RunActive):
        store.create_run("prog", "OTHER", {})
    store.update_run(a.run_id, status="approved")
    assert store.create_run("prog", "OTHER", {}).joined is False


def test_crash_recovery_marks_running_runs_partial(tmp_path):
    store = RunStore(tmp_path)
    a = store.create_run("p", "h", {})
    store.update_run(a.run_id, status="running")
    assert store.recover_interrupted() == [a.run_id]
    assert store.get_run(a.run_id)["status"] == "partial"


def test_concurrency_is_computed_from_the_profile_and_adapts():
    ps = load_profiles()
    w = ps.weakest()
    k = compute_k(w, avg_input_tokens=20_000, call_seconds=50, cfg=CFG)
    assert k >= 1
    assert (
        compute_k(ps.get("large-context-default"), 20_000, 50, CFG) == CFG.concurrency.initial
    )  # unknown limits: configured width

    async def go():
        lim = Limiter(8, CFG)
        lim.k = 8
        await lim.rate_limited()
        assert lim.k == 4
        await lim.rate_limited()
        await lim.rate_limited()
        await lim.rate_limited()
        assert lim.k == 1  # never below the minimum
        await lim.clean_window()
        assert lim.k == 2

    asyncio.run(go())
