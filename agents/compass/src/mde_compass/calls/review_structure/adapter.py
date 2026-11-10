"""``ReviewAdapter``: the engine's ``StructureReviewer`` port over the ``cmp.review_structure`` call. It only reports; the
engine validates each finding against the real ids and facts. A failed call answers with no findings, flagged."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.review_structure.schema import ReviewInput
from mde_compass.calls.review_structure.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.engine.ports import ReviewResult, StructureReviewer, TurnContext


class ReviewAdapter:
    """Implements ``mde_compass.engine.ports.StructureReviewer``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def review(self, ctx: TurnContext, request: ReviewInput) -> ReviewResult:
        result = await self._runner.run(
            SPEC, request, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        return ReviewResult(
            output=result.output,
            used_fallback=result.used_fallback,
            reason=(result.reason or "")[:300],
        )


def build_structure_reviewer(runner: CallRunner, catalog: Catalog) -> StructureReviewer:
    return ReviewAdapter(runner, catalog)


__all__ = ["ReviewAdapter", "build_structure_reviewer"]
