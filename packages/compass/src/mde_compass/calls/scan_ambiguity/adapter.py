"""Adapter: the ``AmbiguityScanner`` port over ``cmp.scan_ambiguity``. Findings only; the Brief is read, never
changed (the port has no way to return a value)."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.scan_ambiguity.digest import build_digest
from mde_compass.calls.scan_ambiguity.schema import ScanInput
from mde_compass.calls.scan_ambiguity.spec import SPEC
from mde_compass.calls.scan_ambiguity.validate import keep_valid
from mde_compass.catalog import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.engine.ports import AmbiguityFinding, TurnContext


class ScanAmbiguityAdapter:
    """Implements ``AmbiguityScanner``: vague, conflicting and undefined statements, as findings."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def scan(self, ctx: TurnContext, brief: Brief) -> list[AmbiguityFinding]:
        items, glossary = build_digest(ctx, brief, self._catalog)
        if not items:
            return []
        inp = ScanInput(language=ctx.language, items=items, glossary_terms=glossary)
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        return [
            AmbiguityFinding(slot_id=f.slot_id, text=f.text, kind=f.kind)
            for f in keep_valid(inp, res.output.findings)
        ]
