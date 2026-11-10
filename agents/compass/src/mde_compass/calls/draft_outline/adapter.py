"""``OutlineAdapter``: the engine's ``StructureOutliner`` port over the ``cmp.draft_outline`` call.

The call returns the plan as the model wrote it; the engine (``engine/structure_draft``) resolves every ref, allocates
the ids and checks every pointer, so nothing here edits the output. A failed call answers with the deterministic floor
and the result is flagged ``used_fallback`` (a fallback is a failure, counted).
"""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.draft_outline.schema import OutlineInput
from mde_compass.calls.draft_outline.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.engine.ports import OutlineResult, StructureOutliner, TurnContext


class OutlineAdapter:
    """Implements ``mde_compass.engine.ports.StructureOutliner``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def outline(self, ctx: TurnContext, request: OutlineInput) -> OutlineResult:
        result = await self._runner.run(
            SPEC, request, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        return OutlineResult(
            output=result.output,
            used_fallback=result.used_fallback,
            reason=(result.reason or "")[:300],
        )


def build_structure_outliner(runner: CallRunner, catalog: Catalog) -> StructureOutliner:
    return OutlineAdapter(runner, catalog)


__all__ = ["OutlineAdapter", "build_structure_outliner"]
