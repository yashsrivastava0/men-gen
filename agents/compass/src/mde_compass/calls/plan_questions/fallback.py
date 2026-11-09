"""The flagged fallback of ``cmp.plan_questions`` (a fallback is a failure: counted, zero in live batches).

* ``wire_fallback`` is what ``CompassCallSpec.fallback`` needs: an *empty* batch. It never invents a question.
* ``fallback_batch`` is what the adapter builds from it: the last-resort cards of the Needs Registry for the
  few most important open needs, every card flagged ``fallback=True`` / ``origin="fallback"``. Follow-up and
  simplify modes return nothing: the engine then assumes visibly and says so, instead of nagging.
* ``closing_fallback`` is the same for the reserved last question (mode ``closing``): one priority card built from
  the closing context's own features (and goals), or the registry card of ``goals.primary`` when the context has
  too little. Flagged ``fallback=True`` and, unlike the cards above, ``origin="closing"``: it is still the closing
  question.
"""

from __future__ import annotations

from mde_compass.calls.plan_questions.schema import HypothesisWire, PlanInput, PlanOutput
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import CardOption, CustomInput, YouDecide
from mde_compass.domain.interview import BatchGroup, PlannedBatch, stored_mode
from mde_compass.engine.cards import fallback_card
from mde_compass.engine.ports import NeedBrief, PlanRequest
from mde_compass.engine.question_validators import (
    CLOSING_MAX_OPTIONS,
    CLOSING_MIN_OPTIONS,
    CLOSING_NEEDS,
)
from mde_compass.engine.terms import tokenize

MAX_FALLBACK_QUESTIONS = 3
OPEN_STATES = ("missing", "partial")


def empty_hypothesis() -> HypothesisWire:
    return HypothesisWire(
        category="",
        subcategory="",
        business_model_candidates=[],
        likely_roles=[],
        likely_features=[],
        hard_parts=[],
        glossary_candidates=[],
        confidence=0.0,
    )


def wire_fallback(inp: PlanInput) -> PlanOutput:
    return PlanOutput(
        domain_hypothesis=empty_hypothesis(),
        questions=[],
        covered_needs=[],
        assumed_instead=[],
        expert_gaps=[],
        backlog=[],
    )


def _open_needs(catalog: Catalog, needs: list[NeedBrief], focus: list[str]) -> list[NeedBrief]:
    order = {sid: i for i, sid in enumerate(catalog.slot_ids_by_order())}
    pool = [
        n
        for n in needs
        if n.state in OPEN_STATES
        and n.need_id in catalog.slots
        and not n.need_id.startswith("domain:")
    ]
    pool.sort(key=lambda n: (n.need_id not in focus, -n.impact, order.get(n.need_id, 999)))
    return pool


MAX_FALLBACK_OPTION_WORDS = 8


def _short(text: str) -> str:
    """An option label of at most eight words (the closing context's statements can be longer)."""
    words = text.split()
    return " ".join(words[:MAX_FALLBACK_OPTION_WORDS])


def _distinct(items: list[str]) -> list[str]:
    seen: set[tuple[str, ...]] = set()
    out: list[str] = []
    for item in items:
        label = _short(item.strip())
        key = tuple(tokenize(label))
        if label and key and key not in seen:
            seen.add(key)
            out.append(label)
    return out


def closing_fallback(
    catalog: Catalog, request: PlanRequest, *, batch_id: str, group_title: str
) -> PlannedBatch:
    """The last question without the model: a priority card from the closing context's own features (and goals,
    when features alone are too few), or the registry card for ``goals.primary``. Flagged, ``origin="closing"``."""
    language = request.language if request.language in ("en", "hi") else "en"
    needs = [n for n in CLOSING_NEEDS if n in catalog.slots]
    closing = request.closing
    labels = _distinct(
        [*(closing.features if closing else []), *(closing.goals if closing else [])]
    )
    number = request.next_card_n
    card_id = f"Q-{number:03d}"
    if len(labels) >= CLOSING_MIN_OPTIONS:
        base = fallback_card(
            "goals.primary", catalog, language=language, card_id=card_id, n=number, of_about=number
        )
        spec = catalog.slots["scope.priority"].answer_spec
        card = base.model_copy(
            update={
                "type": "priority",
                "slots": needs,
                "options": [
                    CardOption(id=chr(ord("A") + i), label=label, basis="suggestion")
                    for i, label in enumerate(labels[:CLOSING_MAX_OPTIONS])
                ],
                "custom_input": CustomInput(
                    enabled=True, placeholder=catalog.t("card.custom_placeholder", language)
                ),
                "you_decide": YouDecide(enabled=True, assumes=None),
                "answer_spec": spec.sufficient if spec else "",
            }
        )
    else:
        card = fallback_card(
            "goals.primary", catalog, language=language, card_id=card_id, n=number, of_about=number
        )
    card = card.model_copy(
        update={
            "frame": "planned",
            "progress": None,
            "batch_id": batch_id,
            "group_id": "G1",
            "need_ids": list(CLOSING_NEEDS),
            "intent": "Learn what matters most in the first version",
            "origin": "closing",
            "fallback": True,
            "priority": 1,
        }
    )
    return PlannedBatch(
        batch_id=batch_id,
        mode=stored_mode(request.mode),
        groups=[BatchGroup(id="G1", title=group_title, cards=[card])],
    )


def fallback_batch(
    catalog: Catalog, request: PlanRequest, *, batch_id: str, group_title: str
) -> PlannedBatch:
    """Last-resort cards for the top open needs, flagged as fallbacks."""
    if request.mode == "closing":
        return closing_fallback(catalog, request, batch_id=batch_id, group_title=group_title)
    if request.mode in ("followup", "simplify"):
        return PlannedBatch(batch_id=batch_id, mode=request.mode)
    language = request.language if request.language in ("en", "hi") else "en"
    limit = max(1, min(request.want_max, MAX_FALLBACK_QUESTIONS))
    picked = _open_needs(catalog, request.needs, request.focus_needs)[:limit]
    cards = []
    for offset, need in enumerate(picked):
        slot = catalog.slots[need.need_id]
        number = request.next_card_n + offset
        base = fallback_card(
            slot.id,
            catalog,
            language=language,
            card_id=f"Q-{number:03d}",
            n=number,
            of_about=number,
        )
        cards.append(
            base.model_copy(
                update={
                    "frame": "planned",
                    "progress": None,
                    "batch_id": batch_id,
                    "group_id": "G1",
                    "need_ids": [slot.id],
                    "intent": f"Learn: {slot.label.en}",
                    "answer_spec": slot.answer_spec.sufficient if slot.answer_spec else "",
                    "origin": "fallback",
                    "fallback": True,
                    "priority": max(1, min(5, 6 - slot.impact)),
                }
            )
        )
    groups = [BatchGroup(id="G1", title=group_title, cards=cards)] if cards else []
    return PlannedBatch(batch_id=batch_id, mode=stored_mode(request.mode), groups=groups)
