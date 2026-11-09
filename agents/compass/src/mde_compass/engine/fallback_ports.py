"""Deterministic placeholder ports for the intake-side calls (documents are a deferred phase).

Until ``cmp.read_chunk``, ``cmp.merge_claims`` and ``cmp.scan_ambiguity`` get their call folders, these keep the
engine's ``Ports`` complete: no document is read, nothing is merged, nothing is flagged. They never invent.
"""

from __future__ import annotations

from mde_compass.domain.brief import Brief
from mde_compass.domain.claims import Statement
from mde_compass.engine.ports import AmbiguityFinding, MergeResult, TurnContext


class NoChunkReader:
    async def read(
        self, ctx: TurnContext, *, chunk_text: str, source_id: str, pointer: str
    ) -> list[Statement]:
        return []


class NoClaimMerger:
    async def merge(
        self, ctx: TurnContext, statements: list[Statement], *, source_id: str
    ) -> MergeResult:
        return MergeResult(claims=[], statement_count=len(statements))


class NoAmbiguityScanner:
    async def scan(self, ctx: TurnContext, brief: Brief) -> list[AmbiguityFinding]:
        return []
