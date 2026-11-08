"""``ProbeHandoffAdapter``: the ``Prober`` port over the call. Pass or fail comes from ``rules.evaluate``."""

from __future__ import annotations

import json

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_text.brief_text import slot_facts, split_list
from mde_compass.calls.compile_text.text_utils import clip
from mde_compass.calls.probe_handoff.rules import evaluate
from mde_compass.calls.probe_handoff.schema import (
    MAX_ITEMS,
    Digest,
    DigestApp,
    DigestFeature,
    DigestRole,
    ProbeHandoffInput,
    ProbeHandoffOutput,
)
from mde_compass.calls.probe_handoff.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.engine.ports import ProbeGap, ProbeKind, ProbeResult, TurnContext

TEXT_CHARS = 300


def build_digest(ctx: TurnContext, brief: Brief, catalog: Catalog) -> Digest:
    """The compact digest a downstream planner would be handed: the Brief reduced, never the chat."""
    facts = slot_facts(ctx, brief, catalog)

    def text(slot_id: str) -> str:
        return clip(facts[slot_id].text, TEXT_CHARS) if slot_id in facts else ""

    def items(*slot_ids: str) -> list[str]:
        return [i for s in slot_ids for i in split_list(text(s))]

    constraints = [
        f"{facts[s].label}: {clip(facts[s].text, TEXT_CHARS)}"
        for s in catalog.slots
        if s.startswith("constraints.") and s in facts
    ]
    return Digest(
        idea=text("idea.summary"),
        category=text("idea.category") or text("idea.subcategory"),
        business_model=text("idea.business_model"),
        geography=text("context.geo"),
        audience=split_list(text("audience.segments")),
        apps=[DigestApp(name=a.name, purpose=a.purpose) for a in brief.apps][:MAX_ITEMS],
        roles=[DigestRole(name=r.name_in_user_words, goal=r.goal) for r in brief.roles][:MAX_ITEMS],
        features=[
            DigestFeature(name=f.name, intent=f.intent, priority=f.priority, status=f.scope_status)
            for f in brief.scope
        ][:MAX_ITEMS],
        glossary=[g.term for g in brief.glossary][:MAX_ITEMS],
        references=[r.label for r in brief.references][:MAX_ITEMS],
        tone_words=items("look.tone_words"),
        brand_signals=items("brand.name", "brand.assets", "look.visual_references"),
        locales=list(brief.meta.locales) or items("locales"),
        constraints=constraints[:MAX_ITEMS],
    )


def plan_lines(inp: ProbeHandoffInput, out: ProbeHandoffOutput) -> list[str]:
    """The plan the canary produced, as short lines (queries / journeys and screens / tone and direction)."""
    if inp.kind == "research":
        return [f"{q.query} [{q.region}]" for q in out.plan_queries if q.query.strip()]
    if inp.kind == "blueprint":
        lines = [
            f"Journey: {j.name}: {' > '.join(s for s in j.steps if s.strip())}"
            for j in out.journeys
        ]
        return lines + [
            f"Screen: {s.name}" for s in out.screens if s.first_release and s.name.strip()
        ]
    lines = []
    if out.tone.strip():
        lines.append(f"Tone: {out.tone.strip()}")
    if out.direction.strip():
        lines.append(f"Direction: {out.direction.strip()}")
    return lines


def to_result(
    inp: ProbeHandoffInput, out: ProbeHandoffOutput, valid_slots: frozenset[str]
) -> ProbeResult:
    verdict = evaluate(inp, out, valid_slots)
    gaps: list[ProbeGap] = []
    for g in verdict.gaps:
        suggested = json.loads(g.suggested_value_json) if g.suggested_value_json else None
        gaps.append(
            ProbeGap(question=g.question, slot_id=g.slot_id, fix=g.fix, suggested_value=suggested)
        )
    return ProbeResult(kind=inp.kind, passed=verdict.passed, gaps=gaps, plan=plan_lines(inp, out))


class ProbeHandoffAdapter:
    """Implements ``mde_compass.engine.ports.Prober``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog
        self._slots = frozenset(catalog.slots)

    async def probe(self, ctx: TurnContext, kind: ProbeKind, brief: Brief) -> ProbeResult:
        inp = ProbeHandoffInput(
            kind=kind, language=ctx.language, digest=build_digest(ctx, brief, self._catalog)
        )
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        return to_result(inp, res.output, self._slots)
