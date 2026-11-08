"""``UnderstandAdapter``: the engine's ``Understander`` port over the ``cmp.understand_turn`` call."""

from __future__ import annotations

import json
from typing import Literal

from mde_compass.calls.base import CallRunner
from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    PendingCardWire,
    PendingOptionWire,
    SlotCurrentWire,
    SlotHintWire,
    TurnWire,
    UnderstandInput,
    UnderstandOutput,
)
from mde_compass.calls.understand_turn.spec import SPEC
from mde_compass.calls.understand_turn.validate import sanitize, validate
from mde_compass.calls.understand_turn.values import ValueError_, parse_json, validate_value
from mde_compass.catalog.loader import Catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.cards import Card
from mde_compass.engine.ports import SlotUpdate, TurnContext, TurnUnderstanding
from mde_core.redact import redact_text

MAX_HINTS = 25


def _pending(card: Card | None) -> PendingCardWire | None:
    if card is None:
        return None
    return PendingCardWire(
        card_id=card.id,
        frame=card.frame,
        slots=list(card.slots),
        options=[
            PendingOptionWire(
                id=o.id, label=o.label, sets_json=json.dumps(o.sets, ensure_ascii=False)
            )
            for o in card.options
        ],
    )


def select_hints(catalog: Catalog, ctx: TurnContext) -> list[SlotHintWire]:
    """At most 25 slots a message could plausibly touch: the pending card's, those already filled (so a
    correction can reach them), then the rest in dependency order (must slots first)."""
    pending = list(ctx.pending_card.slots) if ctx.pending_card else []
    filled = [s.slot_id for s in ctx.slots if s.value not in (None, "", [])]
    order = catalog.slot_ids_by_order()
    musts = [s for s in order if catalog.slots[s].level == "must"]
    rest = [s for s in order if catalog.slots[s].level != "generated"]
    chosen: list[str] = []
    for sid in (*pending, *filled, *musts, *rest):
        if sid in catalog.slots and sid not in chosen:
            chosen.append(sid)
        if len(chosen) >= MAX_HINTS:
            break
    return [
        SlotHintWire(
            slot_id=sid,
            label=catalog.slots[sid].label.en,
            value_kind=catalog.slots[sid].value_schema.kind,
            options=list(catalog.slots[sid].value_schema.options),
            fields=list(catalog.slots[sid].value_schema.fields),
        )
        for sid in chosen
    ]


def build_input(
    catalog: Catalog, ctx: TurnContext, text: str, input_mode: Literal["text", "voice"]
) -> tuple[UnderstandInput, bool]:
    """The wire input (secrets redacted; recent turns capped) and whether a secret was found."""
    clean, kinds = redact_text(text)
    turns = ctx.recent_turns[-DEFAULT_CONFIG.gate.history_slice_turns :]
    return (
        UnderstandInput(
            text=clean,
            input_mode=input_mode,
            language=ctx.language,
            pending_card=_pending(ctx.pending_card),
            current_slots=[
                SlotCurrentWire(
                    slot_id=s.slot_id,
                    value_json=json.dumps(s.value, ensure_ascii=False),
                    state=s.state,
                )
                for s in ctx.slots
                if s.state != "empty"
            ],
            slot_hints=select_hints(catalog, ctx),
            recent_turns=[TurnWire(role=t.role, text=redact_text(t.text)[0]) for t in turns],
        ),
        bool(kinds),
    )


def to_domain(
    catalog: Catalog, out: UnderstandOutput, *, secret: bool = False
) -> TurnUnderstanding:
    """Wire output (already sanitised) to the domain object; values are parsed and validated again."""
    updates: list[SlotUpdate] = []
    for u in out.slot_updates:
        slot = catalog.slots[u.slot_id]
        try:
            value = validate_value(slot, parse_json(u.value_json))
        except ValueError_:  # pragma: no cover - sanitise already checked
            continue
        updates.append(
            SlotUpdate(
                slot_id=u.slot_id,
                value=value,
                quote=u.quote,
                confidence=u.confidence,
                basis=u.basis,
                remove=u.remove,
            )
        )
    flags = list(dict.fromkeys([*out.safety_flags, *(["secret"] if secret else [])]))
    return TurnUnderstanding(
        intent=out.intent,
        slot_updates=updates,
        references=out.references,
        language=out.language or "en",
        safety_flags=flags,
        ambiguous=out.ambiguous,
        confidence=out.confidence,
    )


class UnderstandAdapter:
    """Implements ``mde_compass.engine.ports.Understander``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def understand(
        self, ctx: TurnContext, text: str, *, input_mode: Literal["text", "voice"] = "text"
    ) -> TurnUnderstanding:
        wire, secret = build_input(self._catalog, ctx, text, input_mode)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out, _dropped = sanitize(wire, result.output, self._catalog)
        if validate(wire, out, self._catalog):  # still not acceptable: the deterministic reading
            out = SPEC.fallback(wire)
            out, _ = sanitize(wire, out, self._catalog)
        out = out.model_copy(update={"language": out.language or lx.detect_language(wire.text)})
        return to_domain(self._catalog, out, secret=secret)
