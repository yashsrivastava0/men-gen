"""Deterministic placeholder ports for the intake-side calls (documents are a deferred phase).

Until ``cmp.read_chunk``, ``cmp.merge_claims`` and ``cmp.scan_ambiguity`` get their call folders, these keep the
engine's ``Ports`` complete: no document is read, nothing is merged, nothing is flagged. They never invent.
"""

from __future__ import annotations

from mde_compass.domain.brief import Brief
from mde_compass.domain.claims import Statement
from mde_compass.domain.interview import PlannedBatch
from mde_compass.engine.ports import (
    AmbiguityFinding,
    AuditRequest,
    AuditResult,
    BriefCompilation,
    CompileRequest,
    MergeResult,
    PlanRequest,
    PlanResult,
    RephraseRequest,
    RephraseResult,
    SummaryRequest,
    SummaryResult,
    TurnContext,
)


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


# --- the AI-planned interview: placeholders until each call folder lands (build prompt 02) --------------
# They never invent a question. Every result says ``used_fallback=True``: a fallback is a failure and is
# counted, so an engine wired to a placeholder can never be mistaken for the product.


class UnavailableQuestionPlanner:
    async def plan(self, ctx: TurnContext, request: PlanRequest) -> PlanResult:
        batch = PlannedBatch(batch_id="BAT-000", mode=request.mode)
        return PlanResult(
            batch=batch, used_fallback=True, reason="cmp.plan_questions is not built yet"
        )


class UnavailableQuestionAuditor:
    async def audit(self, ctx: TurnContext, request: AuditRequest) -> AuditResult:
        return AuditResult(
            verdicts=[], used_fallback=True, reason="cmp.audit_questions is not built yet"
        )


class UnavailableSummaryUpdater:
    async def update(self, ctx: TurnContext, request: SummaryRequest) -> SummaryResult:
        return SummaryResult(summary=request.previous, used_fallback=True)


class UnavailableBriefCompiler:
    async def compile_brief(self, ctx: TurnContext, request: CompileRequest) -> BriefCompilation:
        return BriefCompilation(used_fallback=True)


class UnavailableQuestionRephraser:
    async def rephrase(self, ctx: TurnContext, request: RephraseRequest) -> RephraseResult:
        return RephraseResult(
            question=request.card.question,
            why_we_ask=request.card.why_we_ask,
            options=list(request.card.options),
            used_fallback=True,
        )
