"""``WriteCardAdapter``: the engine's ``CardWriter`` port over the ``cmp.write_card`` call."""

from __future__ import annotations

import json

from mde_compass.calls.base import CallRunner
from mde_compass.calls.write_card.schema import (
    FrameWire,
    KnownSlotWire,
    ProfileWire,
    SlotDefWire,
    WriteCardInput,
    WriteCardOutput,
)
from mde_compass.calls.write_card.spec import SPEC
from mde_compass.calls.write_card.validate import validate_card
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import CardOption
from mde_compass.engine.ports import CardDraft, CardRequest, TurnContext
from mde_core.redact import redact_text

MAX_KNOWN = 12
MAX_EVIDENCE = 8


def build_input(catalog: Catalog, ctx: TurnContext, req: CardRequest) -> WriteCardInput:
    """The wire input: frame and slot definitions from the catalog, compact known slots, redacted evidence."""
    frame = catalog.frames[req.frame_id]
    profile = ctx.profile
    known = [s for s in ctx.slots if s.value not in (None, "", []) and s.slot_id not in req.slots]
    return WriteCardInput(
        frame=FrameWire(
            id=frame.id,
            purpose=frame.purpose,
            guidance=frame.guidance,
            card_type=req.card_type,
            option_count=frame.option_count
            if req.card_type in ("choose_one", "look_signals")
            else None,
            max_question_words=frame.max_question_words,
            max_option_words=frame.max_option_words,
        ),
        slots=[
            SlotDefWire(
                slot_id=sid,
                label=catalog.slots[sid].label.en,
                value_kind=catalog.slots[sid].value_schema.kind,
                options=list(catalog.slots[sid].value_schema.options),
                fields=list(catalog.slots[sid].value_schema.fields),
            )
            for sid in req.slots
            if sid in catalog.slots
        ],
        option_source=req.option_source,
        hints=[h for h in req.hints if h.strip()][:20],
        evidence=[redact_text(e)[0] for e in req.evidence][:MAX_EVIDENCE],
        must_confirm=req.must_confirm,
        language=ctx.language,
        avoid_labels=list(req.avoid_labels),
        profile=ProfileWire(
            expertise=profile.expertise if profile else "non_technical",
            formality=profile.formality if profile else "casual",
            language=ctx.language,
        ),
        known_slots=[
            KnownSlotWire(
                slot_id=s.slot_id, value_json=json.dumps(s.value, ensure_ascii=False)[:300]
            )
            for s in known[:MAX_KNOWN]
        ],
        question_number=ctx.question_n + 1,
    )


def to_draft(out: WriteCardOutput, language: str) -> CardDraft:
    options: list[CardOption] = []
    for o in out.options:
        sets = json.loads(o.sets_json)
        options.append(
            CardOption(
                id=o.id,
                label=o.label.strip(),
                basis=o.basis,
                recommended=o.recommended,
                reason=o.reason.strip(),
                sets=sets if isinstance(sets, dict) else {},
                ticked=o.ticked,
            )
        )
    return CardDraft(
        question=out.question.strip(),
        why_we_ask=out.why_we_ask.strip(),
        options=options,
        placeholder=out.placeholder.strip(),
        language=language,
    )


class WriteCardAdapter:
    """Implements ``mde_compass.engine.ports.CardWriter``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def write(self, ctx: TurnContext, request: CardRequest) -> CardDraft:
        wire = build_input(self._catalog, ctx, request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out = result.output
        if not result.used_fallback and validate_card(wire, out, self._catalog):
            out = SPEC.fallback(wire)  # the model's card broke a card rule: the plain, safe card
        return to_draft(out, ctx.language)
