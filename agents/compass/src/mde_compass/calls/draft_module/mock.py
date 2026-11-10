"""Mock handler of ``cmp.draft_module``: the deterministic draft with a ``[mock]`` tag on every purpose. For offline tests
and demos only."""

from __future__ import annotations

from mde_compass.calls.draft_module.schema import ModuleInput, ModuleOutput
from mde_compass.calls.structure_floor import floor_modules

TAG = "[mock]"


def handle(inp: ModuleInput) -> ModuleOutput:
    out = floor_modules(inp)
    return out.model_copy(
        update={
            "screens": [s.model_copy(update={"purpose": f"{TAG} {s.purpose}"}) for s in out.screens]
        }
    )
