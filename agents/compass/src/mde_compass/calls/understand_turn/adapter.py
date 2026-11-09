"""``UnderstandAdapter``: the engine's ``Understander`` port over the ``cmp.understand_turn`` call.

Version 2 takes the question being answered (``focus``), the needs table (``needs``) and the fact digest and
returns facts, flags, glossary terms and contradictions next to the slot updates. Called without those three (the
legacy path) it behaves as version 1 did: the same slot updates, intent, references, language and safety flags.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mde_compass.calls.base import CallRunner
from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    NeedWire,
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
from mde_compass.domain.interview import FactEffect
from mde_compass.domain.situation import GlossaryCandidate
from mde_compass.engine.ports import (
    AnswerFocus,
    ExtractedFact,
    NeedBrief,
    SlotUpdate,
    TurnContext,
    TurnUnderstanding,
)
from mde_core.redact import redact_text

MAX_HINTS = 25
MAX_NEEDS = 20
"""At most this many needs go on the wire: the focus needs first, then the open ones by impact."""
MAX_DIGEST_CHARS = 40_000
"""Safety bound on the fact digest (the engine already fits it to its token budget)."""
OPEN_STATES = ("missing", "partial")


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


def select_hints(
    catalog: Catalog, ctx: TurnContext, focus_slots: Sequence[str] = ()
) -> list[SlotHintWire]:
    """At most 25 slots a message could plausibly touch: the focus question's, the pending card's, those already
    filled (so a correction can reach them), then the rest in dependency order (must slots first)."""
    pending = [*focus_slots, *(ctx.pending_card.slots if ctx.pending_card else [])]
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


def _need_order(need: NeedBrief, focus_ids: Sequence[str]) -> tuple[int, int, int]:
    """Focus needs first, then open ones (missing, partial) by impact, then the rest."""
    return (
        0 if need.need_id in focus_ids else 1,
        0 if need.state in OPEN_STATES else 1,
        -need.impact,
    )


def need_wires(needs: Sequence[NeedBrief], focus_ids: Sequence[str]) -> list[NeedWire]:
    """The needs table on the wire: ordered by ``_need_order`` (stable) and capped at ``MAX_NEEDS``."""
    ordered = sorted(needs, key=lambda n: _need_order(n, focus_ids))
    return [
        NeedWire(
            need_id=n.need_id,
            label=n.label,
            sufficient=n.sufficient,
            aspects=list(n.aspects),
            state=n.state,
            missing_aspects=list(n.missing_aspects),
        )
        for n in ordered[:MAX_NEEDS]
    ]


def _clip_lines(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a line end (a fact line is never split in the middle)."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rpartition("\n")[0]
    return cut or text[:limit]


def build_input(
    catalog: Catalog,
    ctx: TurnContext,
    text: str,
    input_mode: Literal["text", "voice"],
    *,
    focus: AnswerFocus | None = None,
    needs: Sequence[NeedBrief] | None = None,
    fact_digest: str = "",
) -> tuple[UnderstandInput, bool]:
    """The wire input (secrets redacted; recent turns capped) and whether a secret was found.

    For a tap with no typed text, the selected option labels stand in for the message (the person's words are
    the option they chose). Without ``focus``, ``needs`` and ``fact_digest`` the v2 fields are empty."""
    labels = [redact_text(lbl)[0] for lbl in focus.selected_labels] if focus else []
    message = text if text.strip() or not labels else "; ".join(labels)
    clean, kinds = redact_text(message)
    digest = redact_text(_clip_lines(fact_digest, MAX_DIGEST_CHARS))[
        0
    ]  # not a "secret" flag: not this turn
    focus_ids = list(focus.need_ids) if focus else []
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
            slot_hints=select_hints(catalog, ctx, focus_ids),
            recent_turns=[TurnWire(role=t.role, text=redact_text(t.text)[0]) for t in turns],
            focus_card_id=focus.card_id if focus else "",
            focus_question=redact_text(focus.question)[0] if focus else "",
            focus_intent=focus.intent if focus else "",
            focus_need_ids=focus_ids,
            focus_selected_labels=labels,
            needs=need_wires(needs or [], focus_ids),
            fact_digest=digest,
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
    flag_list: list[str] = [*out.safety_flags, *(["secret"] if secret else [])]
    flags = list(dict.fromkeys(flag_list))
    facts = [
        ExtractedFact(
            type=f.type,
            statement=f.statement,
            quote=f.quote,
            topics=list(f.topics),
            entities=list(f.entities),
            consumers=list(f.consumers),
            confidence=f.confidence,
            aspects=list(f.aspects),
            effects=[
                FactEffect(slot_id=e.slot_id, value_json=e.value_json, mode=e.mode)
                for e in f.effects
            ],
            corrects=f.corrects or None,
        )
        for f in out.facts
    ]
    return TurnUnderstanding(
        intent=out.intent,
        slot_updates=updates,
        references=out.references,
        language=out.language or "en",
        safety_flags=flags,
        ambiguous=out.ambiguous,
        confidence=out.confidence,
        facts=facts,
        flags=list(out.flags),
        glossary_terms=[
            GlossaryCandidate(term=g.term, synonyms=list(g.synonyms)) for g in out.glossary_terms
        ],
        contradicts=list(out.contradicts),
    )


class UnderstandAdapter:
    """Implements ``mde_compass.engine.ports.Understander``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def understand(
        self,
        ctx: TurnContext,
        text: str,
        *,
        input_mode: Literal["text", "voice"] = "text",
        focus: AnswerFocus | None = None,
        needs: list[NeedBrief] | None = None,
        fact_digest: str = "",
    ) -> TurnUnderstanding:
        wire, secret = build_input(
            self._catalog,
            ctx,
            text,
            input_mode,
            focus=focus,
            needs=needs,
            fact_digest=fact_digest,
        )
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out, _dropped = sanitize(wire, result.output, self._catalog)
        if validate(wire, out, self._catalog):  # still not acceptable: the deterministic reading
            out = SPEC.fallback(wire)
            out, _ = sanitize(wire, out, self._catalog)
        out = out.model_copy(update={"language": out.language or lx.detect_language(wire.text)})
        return to_domain(self._catalog, out, secret=secret)
