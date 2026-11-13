"""Mock handler of ``cmp.draft_outline``: the deterministic outline with a ``[mock]`` tag on every purpose, so a mock session
is never mistaken for the product. For offline tests and demos only."""

from __future__ import annotations

from mde_compass.calls.draft_outline.schema import OutlineInput, OutlineOutput
from mde_compass.calls.structure_floor import floor_outline

TAG = "[mock]"


def handle(inp: OutlineInput) -> OutlineOutput:
    out = floor_outline(inp)
    return out.model_copy(
        update={
            "modules": [
                m.model_copy(update={"purpose": f"{TAG} {m.purpose}"}) for m in out.modules
            ],
            "entities": [
                e.model_copy(update={"purpose": f"{TAG} {e.purpose}"}) for e in out.entities
            ],
        }
    )
