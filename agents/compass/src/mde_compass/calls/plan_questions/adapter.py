"""``PlanAdapter``: the engine's ``QuestionPlanner`` port over the ``cmp.plan_questions`` call."""

from __future__ import annotations

import json

from mde_compass.calls.base import CallRunner
from mde_compass.calls.plan_questions.fallback import fallback_batch
from mde_compass.calls.plan_questions.schema import (
    NeedWire,
    PlanInput,
    PlanOutput,
    QuestionWire,
)
from mde_compass.calls.plan_questions.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import Card, CardOption, CardType, CustomInput, YouDecide
from mde_compass.domain.interview import (
    AssumedInstead,
    BacklogItem,
    BatchGroup,
    PlannedBatch,
    stored_mode,
)
from mde_compass.domain.situation import DomainHypothesis, GlossaryCandidate, HypothesisFeature
from mde_compass.engine.ports import NeedBrief, PlanRequest, PlanResult, TurnContext
from mde_core.redact import redact_text

PLACEHOLDER_BATCH_ID = "BAT-000"
"""The adapter does not own ids: the engine replaces this with the stored batch id (``BatchRepo.next_id``)."""
MAX_OPTIONS = 8
_TYPE: dict[str, CardType] = {
    "choose_one": "choose_one",
    "pick_many": "pick_many",
    "priority": "priority",
    "confirm": "confirm",
    "text": "open_prompt",
    "quick_form": "quick_form",
    "upload_or_link": "upload_or_link",
}


def _lang(request: PlanRequest) -> str:
    return request.language if request.language in ("en", "hi") else "en"


def _assumed_text(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _need_wire(catalog: Catalog, need: NeedBrief, lang: str) -> NeedWire:
    slot = catalog.slots.get(need.need_id)
    card_type = slot.fallback_card.type if slot else "open_prompt"
    labels = [o.label.get(lang) for o in slot.fallback_card.options] if slot else []
    return NeedWire(
        need_id=need.need_id,
        label=need.label,
        impact=need.impact,
        level=slot.level if slot else "should",
        state=need.state,
        sufficient=need.sufficient,
        aspects=list(need.aspects),
        missing_aspects=list(need.missing_aspects),
        card_type=card_type,
        option_labels=labels,
        assumed_value=_assumed_text(need.assumed_value),
    )


def build_input(catalog: Catalog, request: PlanRequest) -> PlanInput:
    """The wire input. Everything that carries the user's words is secret-redacted first."""
    lang = _lang(request)
    ctx = request.context
    focus = request.focus
    answer = ""
    if focus is not None:
        answer = " | ".join([*focus.selected_labels, focus.answer_text]).strip(" |")
    if request.mode == "closing":
        raise NotImplementedError("planner mode 'closing' is built by the calls stream (ADR 0016)")
    return PlanInput(
        mode=request.mode,
        language=request.language,
        project_card=redact_text(ctx.project_card)[0],
        fact_digest=redact_text(ctx.fact_digest)[0],
        asked_list=ctx.asked_list,
        last_qa=redact_text(ctx.last_qa)[0],
        project_terms=list(request.project_terms),
        needs=[_need_wire(catalog, n, lang) for n in request.needs],
        want_min=request.want_min,
        want_max=request.want_max,
        group_min=request.group_min,
        group_max=request.group_max,
        remaining_budget=request.remaining_budget,
        focus_needs=list(request.focus_needs),
        focus_card_id=focus.card_id if focus else "",
        focus_question=focus.question if focus else "",
        focus_answer=redact_text(answer)[0],
        missing_aspects=list(request.missing_aspects),
        pivot_from=request.pivot_from or "",
        style_notes=list(request.style_notes),
        next_card_n=request.next_card_n,
    )


_FREE_TYPES = {"text", "quick_form", "upload_or_link"}


def _card(
    catalog: Catalog, q: QuestionWire, *, number: int, group_id: str, request: PlanRequest
) -> Card:
    options = [
        CardOption(
            id=chr(ord("A") + i),
            label=o.label,
            basis=o.basis,
            recommended=bool(q.recommended) and o.label == q.recommended,
            reason=o.reason,
            description=o.description,
        )
        for i, o in enumerate(q.options[:MAX_OPTIONS])
        if q.type not in _FREE_TYPES
    ]
    recommended = next((o for o in options if o.recommended), None)
    followup = request.mode == "followup"
    return Card(
        id=f"Q-{number:03d}",
        frame="planned",
        slots=[s for s in q.serves if s in catalog.slots],
        type=_TYPE[q.type],
        question=q.text,
        why_we_ask=q.why_we_ask,
        options=options,
        custom_input=CustomInput(enabled=True, placeholder=q.examples[0] if q.examples else ""),
        you_decide=YouDecide(enabled=True, assumes=recommended.id if recommended else None),
        progress=None,
        language=request.language,
        group_id=group_id,
        need_ids=list(q.serves),
        intent=q.intent,
        unlocks=list(q.unlocks),
        answer_spec=q.answer_spec,
        followup_hint=q.followup_hint,
        examples=list(q.examples),
        parent_card_id=request.focus.card_id if followup and request.focus else None,
        depth=request.followup_depth if followup else 0,
        origin="followup" if followup else "planner",
        priority=max(
            1, min(5, q.priority)
        ),  # an ordering hint: an out-of-range value is clamped, never fatal
    )


def to_batch(
    catalog: Catalog, out: PlanOutput, request: PlanRequest, *, batch_id: str = PLACEHOLDER_BATCH_ID
) -> PlannedBatch:
    """Wire output to the domain batch: ids are assigned here in order (the model's own ids are ignored, so two
    batches can never collide), options get letters, and the question type maps to the card type."""
    number = request.next_card_n
    order: list[str] = []
    titles: dict[str, str] = {}
    by_group: dict[str, list[Card]] = {}
    for q in out.questions:
        key = q.group_id or "G1"
        if key not in by_group:
            order.append(key)
            titles[key] = q.group_title
            by_group[key] = []
        gid = f"G{order.index(key) + 1}"
        by_group[key].append(_card(catalog, q, number=number, group_id=gid, request=request))
        number += 1
    groups = [
        BatchGroup(id=f"G{i}", title=titles[key], cards=by_group[key])
        for i, key in enumerate(order, start=1)
    ]
    hyp = out.domain_hypothesis
    hypothesis = None
    if request.mode == "initial" and hyp.category.strip():
        hypothesis = DomainHypothesis(
            category=hyp.category,
            subcategory=hyp.subcategory,
            business_model_candidates=list(hyp.business_model_candidates),
            likely_roles=list(hyp.likely_roles),
            likely_features=[
                HypothesisFeature(family="", name=f.name, intent=f.intent, custom=f.custom)
                for f in hyp.likely_features
            ],
            hard_parts=list(hyp.hard_parts),
            glossary_candidates=[
                GlossaryCandidate(term=g.term, synonyms=list(g.synonyms))
                for g in hyp.glossary_candidates
            ],
            confidence=max(0.0, min(1.0, hyp.confidence)),
        )
    return PlannedBatch(
        batch_id=batch_id,
        mode=stored_mode(request.mode),
        groups=groups,
        covered_needs=list(out.covered_needs),
        assumed_instead=[
            AssumedInstead(need_id=a.need_id, assumption=a.assumption, reason=a.reason)
            for a in out.assumed_instead
        ],
        expert_gaps=list(out.expert_gaps),
        backlog=[
            BacklogItem(topic=b.topic, intent=b.intent, serves=list(b.serves)) for b in out.backlog
        ],
        domain_hypothesis=hypothesis,
    )


class PlanAdapter:
    """Implements ``mde_compass.engine.ports.QuestionPlanner``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def plan(self, ctx: TurnContext, request: PlanRequest) -> PlanResult:
        wire = build_input(self._catalog, request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        if result.used_fallback:
            title = self._catalog.t("interview.group_fallback", _lang(request))
            batch = fallback_batch(
                self._catalog, request, batch_id=PLACEHOLDER_BATCH_ID, group_title=title
            )
            return PlanResult(batch=batch, used_fallback=True, reason=result.reason)
        batch = to_batch(self._catalog, result.output, request)
        return PlanResult(batch=batch, used_fallback=False, repaired=result.repaired)

    async def fallback(self, ctx: TurnContext, request: PlanRequest) -> PlanResult:
        """The flagged registry cards, without calling the model (used when every planned question was rejected)."""
        title = self._catalog.t("interview.group_fallback", _lang(request))
        batch = fallback_batch(
            self._catalog, request, batch_id=PLACEHOLDER_BATCH_ID, group_title=title
        )
        return PlanResult(
            batch=batch, used_fallback=True, reason="every planned question was rejected"
        )


__all__ = ["PlanAdapter", "build_input", "to_batch"]
