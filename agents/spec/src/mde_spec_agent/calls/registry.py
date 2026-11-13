"""All Spec call specs, collected from the lanes (the Verify agent's calls are registered by its own package)."""

from __future__ import annotations

from mde_spec_agent.calls.base import SpecCallSpec


def all_calls() -> list[SpecCallSpec]:
    from mde_spec_agent.lanes import design, product, screens

    calls = [*product.CALLS, *design.CALLS, *screens.CALLS]
    ids = [c.id for c in calls]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate call ids: {dupes}")
    return calls


def all_steps() -> dict:
    from mde_spec_agent.lanes import design, product, screens

    steps: dict = {}
    for lane in (product, design, screens):
        for name, fn in lane.STEPS.items():
            if name in steps:
                raise ValueError(f"duplicate step {name}")
            steps[name] = fn
    return steps
