"""``RephraseAdapter``: the engine's ``QuestionRephraser`` port over the ``cmp.rephrase_question`` call."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.rephrase_question.schema import (
    OptionWire,
    RephraseInput,
    RephraseOutput,
)
from mde_compass.calls.rephrase_question.spec import SPEC
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import Card
from mde_compass.engine.ports import RephraseRequest, RephraseResult, TurnContext
from mde_core.redact import redact_text

RECENT_TURNS = 3  # how many of the person's latest messages the call may see
MAX_RECENT_CHARS = 600


def _clean(text: str) -> str:
    return redact_text(text)[0]


def recent_words(ctx: TurnContext) -> str:
    """The person's last few messages (not the assistant's), secret-redacted and capped."""
    mine = [t.text.strip() for t in ctx.recent_turns if t.role == "user" and t.text.strip()]
    return _clean(" | ".join(mine[-RECENT_TURNS:]))[:MAX_RECENT_CHARS]


def build_input(ctx: TurnContext, request: RephraseRequest) -> RephraseInput:
    """The wire input. Everything that carries the person's words is secret-redacted first."""
    card = request.card
    return RephraseInput(
        language=request.language,
        mode=request.mode,
        intent=_clean(card.intent),
        answer_spec=_clean(card.answer_spec),
        question=_clean(card.question),
        why_we_ask=_clean(card.why_we_ask),
        type=card.type,
        options=[
            OptionWire(label=_clean(o.label), description=_clean(o.description))
            for o in card.options
        ],
        project_terms=list(request.project_terms),
        recent_user_words=recent_words(ctx),
    )


def unchanged(card: Card, *, used_fallback: bool) -> RephraseResult:
    """The original wording, as the domain result (the flagged fallback)."""
    return RephraseResult(
        question=card.question,
        why_we_ask=card.why_we_ask,
        options=list(card.options),
        examples=list(card.examples),
        used_fallback=used_fallback,
    )


def to_result(card: Card, out: RephraseOutput) -> RephraseResult:
    """Map the wire options back onto the ORIGINAL ``CardOption`` objects by index: id, basis, recommended,
    reason, sets and ticked stay; only the label and the description are replaced. If the count differs the
    original card comes back unchanged and flagged (the person's choices must keep their meaning)."""
    if len(out.options) != len(card.options):
        return unchanged(card, used_fallback=True)
    options = [
        original.model_copy(
            update={"label": new.label.strip(), "description": new.description.strip()}
        )
        for original, new in zip(card.options, out.options, strict=True)
    ]
    return RephraseResult(
        question=out.question.strip(),
        why_we_ask=out.why_we_ask.strip() or card.why_we_ask,
        options=options,
        examples=[e.strip() for e in out.examples if e.strip()],
        used_fallback=False,
    )


class RephraseAdapter:
    """Implements ``mde_compass.engine.ports.QuestionRephraser``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def rephrase(self, ctx: TurnContext, request: RephraseRequest) -> RephraseResult:
        wire = build_input(ctx, request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        if result.used_fallback:
            return unchanged(request.card, used_fallback=True)
        return to_result(request.card, result.output)


__all__ = ["RephraseAdapter", "build_input", "to_result", "unchanged"]
