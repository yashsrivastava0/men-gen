"""Adapter: the ``AmbiguityScanner`` port over ``cmp.scan_ambiguity``. Findings only; the Brief is read, never
changed (the port has no way to return a value).

v2 (ADR 0016): the scanner also reads the details the Brief carries (``domain_facts``) and the compiled Brief, and
flags claims that do not add up (unrealistic, a missing dependency, numbers or dates that do not fit together).
``scan_review`` returns the richer ``ReviewFinding`` list (``engine.compile_types.ReviewScanner``); ``scan`` is the
basic port and maps the three new kinds to ``conflicting`` (they say two things do not fit)."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.scan_ambiguity.digest import build_digest, fact_lines
from mde_compass.calls.scan_ambiguity.schema import ScanInput, WireFinding
from mde_compass.calls.scan_ambiguity.spec import SPEC
from mde_compass.calls.scan_ambiguity.validate import keep_valid
from mde_compass.catalog import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.engine.compile_types import ReviewFinding
from mde_compass.engine.ports import AmbiguityFinding, TurnContext

_BASIC_KINDS = ("vague", "conflicting", "undefined")


def _basic_kind(kind: str) -> str:
    return kind if kind in _BASIC_KINDS else "conflicting"


class ScanAmbiguityAdapter:
    """Implements ``AmbiguityScanner`` (and ``ReviewScanner``): what is unclear or does not add up, as findings."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def _findings(self, ctx: TurnContext, brief: Brief) -> list[WireFinding]:
        items, glossary = build_digest(ctx, brief, self._catalog)
        facts = fact_lines(brief)
        if not items and not facts:
            return []
        inp = ScanInput(language=ctx.language, items=items, glossary_terms=glossary, facts=facts)
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        return keep_valid(inp, res.output.findings)

    async def scan_review(self, ctx: TurnContext, brief: Brief) -> list[ReviewFinding]:
        return [
            ReviewFinding(slot_id=f.slot_id, text=f.text, kind=f.kind)
            for f in await self._findings(ctx, brief)
        ]

    async def scan(self, ctx: TurnContext, brief: Brief) -> list[AmbiguityFinding]:
        return [
            AmbiguityFinding(slot_id=f.slot_id, text=f.text, kind=_basic_kind(f.kind))  # type: ignore[arg-type]
            for f in await self._findings(ctx, brief)
        ]
