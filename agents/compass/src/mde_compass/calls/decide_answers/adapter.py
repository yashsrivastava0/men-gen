"""``DecideAdapter``: the engine's ``AnswerDecider`` port over the ``cmp.decide_answers`` call.

The model proposes one decision per question; code keeps it only when it is valid for THAT question (the card id is
one of the request's, the option ids are that item's, the right number of them, a non-empty text for an open
question, no question put to the person) and otherwise replaces just that decision with the deterministic choice.
A decision for a ``must_confirm`` item is kept but its confidence is capped, so the engine lists it for approval.
The result always holds one decision per item, in the request's order.
"""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.decide_answers.fallback import (
    SINGLE_TYPES,
    fallback_decision,
    is_open,
)
from mde_compass.calls.decide_answers.schema import (
    DecideInput,
    DecideOutput,
    DecisionWire,
    ItemWire,
    OptionWire,
)
from mde_compass.calls.decide_answers.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.engine.ports import (
    AnswerDecider,
    DecideItem,
    DecideRequest,
    DecideResult,
    Decision,
    TurnContext,
)
from mde_core.redact import redact_text

MAX_RATIONALE = 160
MAX_ANSWER_CHARS = 400
MUST_CONFIRM_MAX_CONFIDENCE = 0.3
"""A decision for a risky (``must_confirm``) need is kept, but never more confident than this: it is shown as low
confidence and the person is asked to confirm it."""
_QUESTION_MARKS = ("?", "？", "؟")
_MAX_REASON = 300


def _clean(text: str) -> str:
    return redact_text(text)[0]


def _item(item: DecideItem) -> ItemWire:
    return ItemWire(
        card_id=item.card_id,
        question=_clean(item.question),
        card_type=item.card_type,
        needs=list(item.need_ids),
        answer_spec=_clean(item.answer_spec),
        options=[OptionWire(id=o.id, label=_clean(o.label), basis=o.basis) for o in item.options],
        recommended_id=item.recommended_id,
        must_confirm=item.must_confirm,
    )


def build_input(request: DecideRequest) -> DecideInput:
    """The wire input. Everything that carries the person's words is secret-redacted first."""
    return DecideInput(
        language=request.language,
        project_card=_clean(request.project_card),
        fact_digest=_clean(request.fact_digest),
        hypothesis=_clean(request.hypothesis),
        items=[_item(i) for i in request.items],
    )


def _problem(item: ItemWire, d: DecisionWire) -> str:
    """Why this decision cannot be kept for this item, or ``""`` when it can."""
    if is_open(item):
        if d.option_ids:
            return "an open question takes no option ids"
        if not d.answer_text.strip():
            return "an open question needs a proposed answer"
        if any(m in d.answer_text for m in _QUESTION_MARKS):
            return "the answer is a question"
        return ""
    ids = d.option_ids
    known = {o.id for o in item.options}
    if any(i not in known for i in ids):
        return "an option id is not one of the question's options"
    if len(set(ids)) != len(ids):
        return "an option id is repeated"
    if item.card_type in SINGLE_TYPES and len(ids) != 1:
        return f"a {item.card_type} question takes exactly one option, got {len(ids)}"
    if not ids:
        return "a question with options needs at least one option"
    return ""


def _confidence(item: ItemWire, value: float) -> float:
    value = max(0.0, min(1.0, value))
    return min(value, MUST_CONFIRM_MAX_CONFIDENCE) if item.must_confirm else value


def _decision(item: ItemWire, d: DecisionWire) -> Decision:
    """The domain decision for a valid wire decision: rationale cut to 160 characters, the text only for an open
    question, the confidence clamped (and capped for a ``must_confirm`` item)."""
    open_question = is_open(item)
    return Decision(
        card_id=item.card_id,
        option_ids=[] if open_question else list(d.option_ids),
        answer_text=d.answer_text.strip()[:MAX_ANSWER_CHARS] if open_question else "",
        rationale=" ".join(d.rationale.split())[:MAX_RATIONALE].rstrip(),
        confidence=_confidence(item, d.confidence),
    )


def _replacement(item: ItemWire) -> Decision:
    d = fallback_decision(item)
    return Decision(
        card_id=item.card_id,
        option_ids=list(d.option_ids),
        answer_text="",
        rationale="",
        confidence=_confidence(item, d.confidence),
    )


def to_result(inp: DecideInput, out: DecideOutput) -> DecideResult:
    """One decision per item, in order. A decision for an unknown card id is ignored, a second one for the same card
    is ignored, a missing or invalid one is replaced by the deterministic choice and the result is flagged."""
    first: dict[str, DecisionWire] = {}
    for d in out.decisions:
        first.setdefault(d.card_id, d)
    decisions: list[Decision] = []
    replaced: list[str] = []
    for item in inp.items:
        wire = first.get(item.card_id)
        problem = "no decision" if wire is None else _problem(item, wire)
        if wire is not None and not problem:
            decisions.append(_decision(item, wire))
            continue
        decisions.append(_replacement(item))
        replaced.append(f"{item.card_id}: {problem}")
    reason = "; ".join(replaced)[:_MAX_REASON]
    return DecideResult(decisions=decisions, used_fallback=bool(replaced), reason=reason)


def fallback_result(inp: DecideInput, reason: str) -> DecideResult:
    """The deterministic decisions for every item, flagged: the call failed (or was never made)."""
    return DecideResult(
        decisions=[_replacement(item) for item in inp.items],
        used_fallback=True,
        reason=reason[:_MAX_REASON],
    )


class DecideAdapter:
    """Implements ``mde_compass.engine.ports.AnswerDecider``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def decide(self, ctx: TurnContext, request: DecideRequest) -> DecideResult:
        if not request.items:
            return DecideResult(decisions=[])  # nothing to decide: no call, no cost
        wire = build_input(request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        if result.used_fallback:
            return fallback_result(wire, result.reason or "the call failed")
        return to_result(wire, result.output)


def build_answer_decider(runner: CallRunner, catalog: Catalog) -> AnswerDecider:
    """The decider the engine uses (``Ports.answer_decider``), built like every other adapter."""
    return DecideAdapter(runner, catalog)


__all__ = [
    "DecideAdapter",
    "build_answer_decider",
    "build_input",
    "fallback_result",
    "to_result",
]
