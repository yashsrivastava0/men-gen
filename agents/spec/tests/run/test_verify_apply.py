import asyncio

from mde_spec_agent.run.orchestrator import MissingNeed, Orchestrator, Stage, StepDef
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.run.verify_apply import apply_patches
from mde_spec_agent.testing import make_test_context
from mde_spec_core.graph import Rule
from mde_spec_core.synth import make_graph
from mde_spec_core.verify_port import Patch


def test_class_a_locked_value_is_restored_and_nothing_else_is_guessed():
    g = make_graph("tiny")
    g.add("rules", Rule(id="RUL-900", subject="fee", statement="a fee", value="5", unit="percent"))
    patches = [
        Patch(
            node="RUL-900",
            field="value",
            change="set value back to '4'",
            reason="Locked fact",
            cls="A",
        ),
        Patch(node="RUL-900", field="value", change="make it nicer", reason="?", cls="A"),
        Patch(node="SCR-002", field="purpose", change="rewrite", reason="?", cls="B"),
        Patch(node="CF-1", field="confirm_rank", change="move above rank 3", reason="?", cls="A"),
    ]
    res = apply_patches(g, patches)
    assert g.rules["RUL-900"].value == "4" and len(res.applied) == 1
    reasons = {s["reason"] for s in res.skipped}
    assert "unrecognised class A form" in reasons and "SCR-002" in res.degraded_nodes


def test_a_missing_builder_need_ends_the_run_partial_not_failed(tmp_path):
    ctx = make_test_context(tmp_path, make_graph("tiny"))

    async def need(c):
        raise MissingNeed(["BN-06"])

    out = asyncio.run(Orchestrator(ctx.store, [Stage((StepDef("assemble", need),))]).run(ctx))
    assert out.status == "partial" and out.reason == "missing_need"
    assert isinstance(ctx.store, RunStore) and ctx.store.get_run(ctx.run_id)["status"] == "partial"
