"""Adapter: the ``ClaimMerger`` port over ``cmp.merge_claims`` (domain <-> wire, code-enforced rules)."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.merge_claims.assemble import assemble
from mde_compass.calls.merge_claims.fallback import confidence_word, fallback
from mde_compass.calls.merge_claims.rules import load_rules
from mde_compass.calls.merge_claims.schema import MergeInput, MergeOutput, MergeStatement
from mde_compass.calls.merge_claims.spec import SPEC
from mde_compass.catalog import Catalog
from mde_compass.domain.claims import Statement
from mde_compass.engine.ports import MergeResult, TurnContext


class MergeClaimsAdapter:
    """Implements ``ClaimMerger``. One ``Claim`` per statement, always: duplicates are marked, conflicting
    statements are kept and flagged (never dropped silently), open issues are collected as ``tbd``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def merge(
        self, ctx: TurnContext, statements: list[Statement], *, source_id: str
    ) -> MergeResult:
        if not statements:
            return MergeResult(claims=[], statement_count=0)
        inp = to_input(statements, source_id, ctx.language)
        out: MergeOutput
        if len(statements) > load_rules().max_statements:
            out = fallback(
                inp
            )  # too many for one call: the deterministic merger handles all of them
        else:
            res = await self._runner.run(
                SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id
            )
            out = res.output
        return assemble(statements, out, source_id=source_id, slot_ids=set(self._catalog.slots))


def to_input(statements: list[Statement], source_id: str, language: str) -> MergeInput:
    return MergeInput(
        source_id=source_id,
        language=language,
        statements=[
            MergeStatement(
                idx=i,
                type=s.type,
                text=s.text,
                confidence=confidence_word(s.confidence),  # type: ignore[arg-type]
            )
            for i, s in enumerate(statements)
        ],
    )
