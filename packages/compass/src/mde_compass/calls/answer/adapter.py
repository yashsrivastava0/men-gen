"""``AnswerAdapter``: implements the ``Answerer`` port over the ``cmp.answer`` call.

Maps the domain (``TurnContext``) to the compact wire input, runs the call through ``CallRunner`` (which
validates and falls back), makes sure the reply points back to the next step, and returns an
``AnswerResult``. Any failure after the run uses the deterministic fallback.
"""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from mde_compass.calls.answer.fallback import classify, ensure_pointer, fallback
from mde_compass.calls.answer.schema import (
    MAX_ANSWER_CHARS,
    AnswerInput,
    AnswerOutput,
    AnswerSlot,
)
from mde_compass.calls.answer.spec import SPEC
from mde_compass.calls.base import CallRunner
from mde_compass.catalog.loader import Catalog
from mde_compass.engine.ports import AnswerResult, SlotView, TurnContext

MAX_SLOTS = 20
MAX_VALUE_CHARS = 140


def compact_value(value: JsonValue) -> str:
    """A slot value as short plain text (lists joined by commas, objects as ``key: value`` pairs)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str | int | float):
        text = str(value)
    elif isinstance(value, list):
        text = ", ".join(compact_value(v) for v in value[:8] if compact_value(v))
    else:
        text = "; ".join(f"{k}: {compact_value(v)}" for k, v in list(value.items())[:8])
    return text.strip()[:MAX_VALUE_CHARS]


def slot_lines(slots: list[SlotView], catalog: Catalog, language: str) -> list[AnswerSlot]:
    """Filled slots with their plain labels, at most ``MAX_SLOTS``."""
    lang = "hi" if language == "hi" else "en"
    lines: list[AnswerSlot] = []
    for view in slots:
        text = compact_value(view.value)
        if view.state == "empty" or not text:
            continue
        slot = catalog.slots.get(view.slot_id)
        label = slot.label.get(lang) if slot else view.slot_id
        lines.append(AnswerSlot(slot_id=view.slot_id, label=label, value=text))
        if len(lines) >= MAX_SLOTS:
            break
    return lines


def build_input(ctx: TurnContext, question: str, catalog: Catalog) -> AnswerInput:
    """The wire input for one question: situation, slots so far, pending card, product facts."""
    lang = "hi" if ctx.language == "hi" else "en"
    profile = ctx.profile
    card = ctx.pending_card
    return AnswerInput(
        question=question,
        language=ctx.language,
        topic=classify(question),
        slots=slot_lines(ctx.slots, catalog, ctx.language),
        expertise=profile.expertise if profile else "non_technical",
        formality=profile.formality if profile else "casual",
        pending_question=card.question if card else None,
        pending_why=(card.why_we_ask or None) if card else None,
        pending_options=[o.label for o in card.options][:6] if card else [],
        facts=[
            catalog.t("fallback.out_of_scope_price_time", lang),
            catalog.t("fallback.tech_choice_fixed_stack", lang),
        ],
    )


class AnswerAdapter:
    """``Answerer`` over ``cmp.answer``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def answer(self, ctx: TurnContext, question: str) -> AnswerResult:
        wire = build_input(ctx, question, self._catalog)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        try:
            return self._to_domain(result.output, wire)
        except (ValueError, TypeError):
            return self._to_domain(fallback(wire), wire)

    @staticmethod
    def _to_domain(out: AnswerOutput, wire: AnswerInput) -> AnswerResult:
        text = ensure_pointer(out.text, wire).strip()
        if not text or len(text) > MAX_ANSWER_CHARS + 40:
            raise ValueError("answer text empty or too long")
        grounded: list[Any] = list(dict.fromkeys(out.grounded_in))
        return AnswerResult(
            text=text,
            language=out.language or wire.language,
            grounded_in=grounded,
            declined=out.declined,
        )
