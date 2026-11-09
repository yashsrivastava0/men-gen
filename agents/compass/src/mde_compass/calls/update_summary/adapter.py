"""``SummaryAdapter``: the engine's ``SummaryUpdater`` port over the ``cmp.update_summary`` call.

The engine hands over the previous ``InterviewSummary``, one digest line per new fact and the newest fact id; it
gets back the refreshed summary (version = previous + 1) and whether the deterministic merge had to be used.
Whatever the model returns is tidied and re-checked here: a summary that would drop a fact id never replaces
the old one, the merge runs instead and the result says ``used_fallback``.
"""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.update_summary.schema import SummaryInput, SummaryOutput, TopicWire
from mde_compass.calls.update_summary.spec import SPEC
from mde_compass.calls.update_summary.validate import sanitize, validate
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.interview import InterviewSummary, SummaryTopic
from mde_compass.engine.ports import SummaryRequest, SummaryResult, TurnContext
from mde_core.redact import redact_text


def _clean(values: list[str]) -> list[str]:
    return [redact_text(v)[0] for v in values]


def build_input(request: SummaryRequest) -> SummaryInput:
    """The wire input. Everything that carries the person's words is secret-redacted first."""
    return SummaryInput(
        language=request.language,
        previous_topics=[
            TopicWire(
                topic=redact_text(t.topic)[0],
                decisions=_clean(t.decisions),
                open_issues=_clean(t.open_issues),
                vocabulary=_clean(t.vocabulary),
                tone=_clean(t.tone),
                fact_ids=list(t.fact_ids),
            )
            for t in request.previous.topics
        ],
        new_facts_digest=redact_text(request.new_facts_digest)[0],
        through_fact_id=request.through_fact_id,
    )


def to_summary(request: SummaryRequest, out: SummaryOutput) -> InterviewSummary:
    """The wire output as the next version of the summary. The newest-fact id is code's, never the model's."""
    previous = request.previous
    return InterviewSummary(
        version=previous.version + 1,
        topics=[
            SummaryTopic(
                topic=t.topic,
                decisions=list(t.decisions),
                open_issues=list(t.open_issues),
                vocabulary=list(t.vocabulary),
                tone=list(t.tone),
                fact_ids=list(t.fact_ids),
            )
            for t in out.topics
        ],
        through_fact_id=request.through_fact_id or previous.through_fact_id,
    )


class SummaryAdapter:
    """Implements ``mde_compass.engine.ports.SummaryUpdater``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def update(self, ctx: TurnContext, request: SummaryRequest) -> SummaryResult:
        wire = build_input(request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out, _notes = sanitize(wire, result.output)
        used_fallback = result.used_fallback
        if validate(wire, out):  # still loses a fact: the deterministic merge keeps every one
            out, _ = sanitize(wire, SPEC.fallback(wire))
            used_fallback = True
        return SummaryResult(summary=to_summary(request, out), used_fallback=used_fallback)
