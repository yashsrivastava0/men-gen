"""``PlanAdapter``: the engine's ``QuestionPlanner`` port over the ``cmp.plan_questions`` call."""

from __future__ import annotations

import json
import re

from mde_compass.calls.base import CallRunner
from mde_compass.calls.plan_questions.fallback import fallback_batch
from mde_compass.calls.plan_questions.schema import (
    ClosingWire,
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
from mde_compass.engine.question_validators import (
    CLOSING_MAX_OPTIONS,
    CLOSING_MAX_SUGGESTIONS,
    CLOSING_NEEDS,
    candidate_match,
    closing_candidates,
    closing_suggested,
    closing_suggestion_cap,
    is_multi,
)
from mde_core.redact import redact_text

PLACEHOLDER_BATCH_ID = "BAT-000"
"""The adapter does not own ids: the engine replaces this with the stored batch id (``BatchRepo.next_id``)."""
MAX_OPTIONS = 8
MAX_NEEDS = 20
"""The planner sees at most this many open needs (the most important ones): the table is the biggest part of its
input, and a batch asks 3 to 5 questions."""
_OPEN = ("missing", "partial")
# the page cannot render the fields of a form card, so a planned quick form is asked as an open question
_TYPE: dict[str, CardType] = {
    "choose_one": "choose_one",
    "pick_many": "pick_many",
    "priority": "priority",
    "confirm": "confirm",
    "text": "open_prompt",
    "quick_form": "open_prompt",
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
        multi=is_multi(need.need_id, catalog),
        assume=assume_by_default(catalog, need),
        option_labels=labels,
        assumed_value=_assumed_text(need.assumed_value),
    )


def assume_by_default(catalog: Catalog, need: NeedBrief) -> bool:
    """A need with a safe typical default (``ask_policy: infer``) that the person has said nothing about: it is
    assumed and shown, not asked. The ``necessity`` validator rejects a question that serves only such needs."""
    slot = catalog.slots.get(need.need_id)
    return (
        slot is not None
        and slot.ask_policy == "infer"
        and not slot.must_confirm
        and need.state == "missing"
    )


def worth_listing(need: NeedBrief) -> bool:
    """Open: missing or partial. (A need below the necessity floor stays in the table, last: the ``necessity``
    validator, not the table, rejects a question that serves only such needs, and the table is cut at
    ``MAX_NEEDS`` by impact anyway.)"""
    return need.state in _OPEN


def open_needs(catalog: Catalog, request: PlanRequest) -> list[NeedBrief]:
    """The needs the planner is shown: the open ones that matter (see ``worth_listing``; satisfied and assumed needs
    are left out because the facts say it, and a shorter table is a faster call), plus any need it is told to focus
    on; the focus first, then by impact and catalog order, at most ``MAX_NEEDS``. A need with a safe default stays
    in the table, marked ``assume``, so the planner can list it in ``assumed_instead``."""
    order = {sid: i for i, sid in enumerate(catalog.slot_ids_by_order())}
    focus = set(request.focus_needs)
    pool = [n for n in request.needs if worth_listing(n) or n.need_id in focus]
    pool.sort(key=lambda n: (n.need_id not in focus, -n.impact, order.get(n.need_id, 999)))
    return pool[:MAX_NEEDS]


def build_input(catalog: Catalog, request: PlanRequest) -> PlanInput:
    """The wire input. Everything that carries the user's words is secret-redacted first."""
    lang = _lang(request)
    ctx = request.context
    focus = request.focus
    answer = ""
    if focus is not None:
        answer = " | ".join([*focus.selected_labels, focus.answer_text]).strip(" |")
    closing = request.closing
    return PlanInput(
        mode=request.mode,
        language=request.language,
        project_card=redact_text(ctx.project_card)[0],
        fact_digest=redact_text(ctx.fact_digest)[0],
        asked_list=ctx.asked_list,
        last_qa=redact_text(ctx.last_qa)[0],
        project_terms=list(request.project_terms),
        # the closing question is built from the closing context, not from the needs table
        needs=[]
        if request.mode == "closing"
        else [_need_wire(catalog, n, lang) for n in open_needs(catalog, request)],
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
        followup_candidates=list(request.followup_candidates),
        structure_focus=request.structure_focus,
        closing=ClosingWire(
            features=_clean_list(closing.features if closing else []),
            suggested_features=_clean_list(closing.suggested_features if closing else []),
            roles=_clean_list(closing.roles if closing else []),
            goals=_clean_list(closing.goals if closing else []),
            constraints=_clean_list(closing.constraints if closing else []),
            assumptions=_clean_list(closing.assumptions if closing else []),
        ),
    )


def _clean_list(items: list[str]) -> list[str]:
    """Statements the person's words went into: secret-redacted, blanks dropped."""
    return [c for c in (redact_text(i)[0].strip() for i in items) if c]


_FREE_TYPES = {"text", "quick_form", "upload_or_link"}


_ASKED_ID = re.compile(r"\bQ-\d{3,}\b")


def _parent(q: QuestionWire, request: PlanRequest) -> tuple[str | None, int]:
    """``(parent card id, depth)``: the focus question of a follow-up call, or the earlier question this one
    ``refines`` (folded into a bigger batch). A ``refines`` that is not in the asked list is ignored: the question is
    then an ordinary one (never fatal, never a link to a question that does not exist)."""
    if request.mode == "followup":
        return (request.focus.card_id if request.focus else None), request.followup_depth
    target = q.refines.strip()
    if target and target in _ASKED_ID.findall(request.context.asked_list):
        return target, 1
    return None, 0


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
    parent, depth = _parent(q, request)
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
        examples=list(q.examples),
        parent_card_id=parent,
        depth=depth,
        origin="followup" if parent or request.mode == "followup" else "planner",
        priority=max(
            1, min(5, q.priority)
        ),  # an ordering hint: an out-of-range value is clamped, never fatal
    )


def _closing_card(catalog: Catalog, q: QuestionWire, *, number: int, request: PlanRequest) -> Card:
    """The reserved last question: always a priority card with its own two needs and the custom input on. Code, not
    the model, says which options are the person's own: one that matches no own candidate is a suggestion whatever
    the model claimed (one built from the suggested features, or an invented one); invented ones beyond two and
    suggestions beyond the cap are dropped (never fatal)."""
    candidates = closing_candidates(request.closing)
    suggested = closing_suggested(request.closing)
    cap = closing_suggestion_cap(request.closing)
    options: list[CardOption] = []
    not_own = invented = 0
    for o in q.options:
        basis = o.basis
        if candidates or suggested:
            if candidate_match(o.label, candidates):
                basis = "suggestion" if o.basis == "typical" else o.basis
            else:
                from_list = candidate_match(o.label, suggested)
                if not_own >= cap or (not from_list and invented >= CLOSING_MAX_SUGGESTIONS):
                    continue
                not_own += 1
                invented += 0 if from_list else 1
                basis = "suggestion"
        options.append(
            CardOption(
                id=chr(ord("A") + len(options)),
                label=o.label,
                basis=basis,
                description=o.description,
            )
        )
        if len(options) >= CLOSING_MAX_OPTIONS:
            break
    top = next((o for o in options if q.recommended and o.label == q.recommended), None)
    needs = [n for n in CLOSING_NEEDS if n in catalog.slots]
    return Card(
        id=f"Q-{number:03d}",
        frame="planned",
        slots=needs,
        type="priority",
        question=q.text,
        why_we_ask=q.why_we_ask,
        options=options,
        custom_input=CustomInput(enabled=True, placeholder=q.examples[0] if q.examples else ""),
        you_decide=YouDecide(enabled=True, assumes=top.id if top else None),
        progress=None,
        language=request.language,
        group_id="G1",
        need_ids=list(CLOSING_NEEDS),
        intent=q.intent,
        unlocks=list(q.unlocks),
        answer_spec=q.answer_spec,
        examples=list(q.examples),
        origin="closing",
        priority=1,
    )


def _closing_batch(
    catalog: Catalog, out: PlanOutput, request: PlanRequest, batch_id: str
) -> PlannedBatch:
    """Mode closing: the first priority question of the output and nothing else."""
    q = next((q for q in out.questions if q.type == "priority"), None)
    if q is None:
        return PlannedBatch(batch_id=batch_id, mode=stored_mode(request.mode))
    card = _closing_card(catalog, q, number=request.next_card_n, request=request)
    return PlannedBatch(
        batch_id=batch_id,
        mode=stored_mode(request.mode),
        groups=[BatchGroup(id="G1", title=q.group_title, cards=[card])],
        covered_needs=list(CLOSING_NEEDS),
    )


def to_batch(
    catalog: Catalog, out: PlanOutput, request: PlanRequest, *, batch_id: str = PLACEHOLDER_BATCH_ID
) -> PlannedBatch:
    """Wire output to the domain batch: ids are assigned here in order (the model's own ids are ignored, so two
    batches can never collide), options get letters, and the question type maps to the card type."""
    if request.mode == "closing":
        return _closing_batch(catalog, out, request, batch_id)
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
