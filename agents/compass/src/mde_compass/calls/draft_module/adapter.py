"""``ModuleAdapter``: the engine's ``StructureModuleDrafter`` port over the ``cmp.draft_module`` call. The engine resolves
refs and checks pointers; nothing here edits the output. A failed call answers with the deterministic floor, flagged."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.draft_module.schema import ModuleInput
from mde_compass.calls.draft_module.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.engine.ports import ModuleResult, StructureModuleDrafter, TurnContext


class ModuleAdapter:
    """Implements ``mde_compass.engine.ports.StructureModuleDrafter``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def draft(self, ctx: TurnContext, request: ModuleInput) -> ModuleResult:
        if not request.modules:
            raise ValueError("a module draft needs at least one module")
        result = await self._runner.run(
            SPEC, request, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        return ModuleResult(
            output=result.output,
            used_fallback=result.used_fallback,
            reason=(result.reason or "")[:300],
        )


def build_structure_drafter(runner: CallRunner, catalog: Catalog) -> StructureModuleDrafter:
    return ModuleAdapter(runner, catalog)


__all__ = ["ModuleAdapter", "build_structure_drafter"]
