"""The pipeline (doc 15 8.1, doc 18 1.2): code steps from `steps_code`, model-facing steps from the lanes.

    intake -> plan -> [product || design] -> join (gate 2) -> freeze -> (G1.5 gate) -> gloss
           -> [screens || hero || behav] -> copyl -> assemble (+ gate 3) -> [verify, deep] -> g2summary -> (G2)

A step that is not registered (a lane not built yet) is skipped, so the code-only skeleton still runs end to end.
"""

from __future__ import annotations

from collections.abc import Callable

from mde_spec_agent.calls.registry import all_steps

from . import steps_code
from .context import RunContext
from .orchestrator import Stage, StepDef


def g15_needed(ctx: RunContext) -> bool:
    """G1.5 is a hard gate for deep apps and whenever a capacity deferral (or a must-deferral) needs the user's consent."""
    if ctx.graph.meta.profile == "deep":
        return True
    plan = getattr(ctx.derived, "plan", None)
    return bool(plan and plan.consent_required)


async def _noop(ctx: RunContext) -> None:
    """The G1.5 stage does nothing itself: the orchestrator pauses before it until the gate is approved."""


def build_pipeline(
    extra_steps: dict[str, Callable] | None = None, *, verify: Callable | None = None
) -> list[Stage]:
    steps = {
        "intake": steps_code.intake,
        "join": steps_code.join,
        "freeze": steps_code.freeze,
        "assemble": steps_code.assemble_and_check,
        **all_steps(),
        **(extra_steps or {}),
    }
    if verify is not None:
        steps["verify"] = verify

    def stage(
        *names: str, gate_before: str | None = None, gate_when: Callable | None = None
    ) -> Stage | None:
        defs = tuple(StepDef(n, steps[n]) for n in names if n in steps)
        return Stage(defs, gate_before=gate_before, gate_when=gate_when) if defs else None

    layout = [
        stage("intake"),
        stage("plan"),
        stage("product", "design"),
        stage("join"),
        stage("freeze"),
        Stage((StepDef("g15", _noop),), gate_before="g15", gate_when=g15_needed),
        stage("gloss"),
        stage("screens", "hero", "behav"),
        stage("copyl"),
        stage("assemble"),
        stage("verify"),
        stage("g2summary"),
    ]
    return [s for s in layout if s is not None]
