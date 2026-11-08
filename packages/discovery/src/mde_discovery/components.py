"""The LLM-facing components: Understander (C1), BriefInterpreter (C2), Responder (C3),
CardWriter (C5) and ChangeInterpreter (C14).

Each builds the *small* prompt input (only the relevant slots, the last few turns, the rolling
summary), runs the call through :class:`~mde_discovery.llm.Llm` and returns a validated output.
Deterministic fallbacks live in ``llm.FALLBACKS``; nothing here raises for a provider failure.
"""

from __future__ import annotations

import json
from typing import Any

from mde_content import stack
from mde_content.questions import QuestionBanks
from mde_contracts.calls import (
    C1Input,
    C1Output,
    C2Input,
    C2Output,
    C3Input,
    C3Output,
    C5Input,
    C5Output,
    C14Input,
    C14Output,
    Intent,
    OptionChoice,
    OptionLite,
    PendingCardLite,
    SensitiveDomainOption,
    SlotBrief,
    SlotCatalogItem,
)
from mde_contracts.cards import Card, CardInstance, CardOption, OtherInput, SkipSpec
from mde_contracts.common import GateKind
from mde_contracts.content import Catalogs
from mde_discovery.llm import Llm
from mde_discovery.turn import Turn
from mde_discovery.vocab import Vocab

CATALOG_GROUPS = ("look", "money")  # always offered to C1 besides the asked levels


def slot_briefs(turn: Turn) -> list[SlotBrief]:
    """Filled slots as compact prompt lines (value as JSON text)."""
    out: list[SlotBrief] = []
    for slot_id, rec in turn.store.snapshot().items():
        if turn.store.is_filled(slot_id):
            out.append(
                SlotBrief.model_validate(
                    {
                        "slot_id": slot_id,
                        "value": json.dumps(rec.value, ensure_ascii=False),
                        "state": rec.state,
                        "source": rec.source,
                    }
                )
            )
    return out


def pending_lite(instance: CardInstance | None) -> PendingCardLite | None:
    if instance is None:
        return None
    card = instance.card
    return PendingCardLite(
        card_id=card.id,
        slot=card.slot,
        type=card.type,
        text=card.text,
        options=[OptionLite(id=o.id, label=o.label) for o in card.options],
    )


class Understander:
    """C1: intent, slot updates with evidence, references, language, safety flags."""

    def __init__(self, llm: Llm, vocab: Vocab, catalogs: Catalogs) -> None:
        self.llm = llm
        self.vocab = vocab
        self.catalogs = catalogs
        self._archetypes = [
            OptionChoice(id=a.id, label=a.label, description=a.description)
            for a in catalogs.archetypes
        ]

    def catalog(self, turn: Turn) -> list[SlotCatalogItem]:
        archetype = turn.store.text_value("idea.archetype")
        levels = ("must", "should") if turn.mode == "enterprise" else ("must",)
        items: list[SlotCatalogItem] = []
        for sd in self.catalogs.slots:
            if sd.level[turn.mode] in levels or sd.group in CATALOG_GROUPS:
                items.append(
                    SlotCatalogItem(
                        slot_id=sd.slot_id,
                        kind=sd.value_kind,
                        hint=self.vocab.hint(sd.slot_id, archetype),
                    )
                )
        return items

    async def understand(
        self, turn: Turn, text: str, *, attachment_text: str | None = None
    ) -> C1Output:
        inp = C1Input(
            message=text,
            attachment_text=attachment_text,
            language_hint=turn.lang,
            mode=turn.mode,
            state=turn.req.session_state,
            gate_open=turn.req.gate_open,
            pending_card=pending_lite(turn.state.pending_card),
            slots=slot_briefs(turn),
            slot_catalog=self.catalog(turn),
            archetypes=self._archetypes,
            recent_turns=turn.prompt_turns(),
            summary=turn.state.summary,
        )
        out, used_fallback, calls = await self.llm.run(turn.ctx, "C1.understand", inp)
        turn.llm_calls += calls
        _note_slow(turn, used_fallback)
        assert isinstance(out, C1Output)
        return out


class BriefInterpreter:
    """C2: classify the first idea or a long upload; flag sensitive domains and unsupported types."""

    def __init__(self, llm: Llm, catalogs: Catalogs) -> None:
        self.llm = llm
        self._archetypes = [
            OptionChoice(id=a.id, label=a.label, description=a.description)
            for a in catalogs.archetypes
        ]
        self._domains = [
            SensitiveDomainOption(id=d.id, label=d.label) for d in catalogs.sensitive_domains
        ]

    async def interpret(
        self, turn: Turn, idea_text: str, document_text: str | None = None
    ) -> C2Output:
        profile = stack.load_stack_profile()
        inp = C2Input(
            idea_text=idea_text,
            document_text=document_text,
            archetypes=self._archetypes,
            sensitive_domains=self._domains,
            unsupported_types=[
                OptionChoice(id=u.id, label=u.label, description=u.reason)
                for u in profile.unsupported_types
            ],
            language=turn.lang,
        )
        out, used_fallback, calls = await self.llm.run(turn.ctx, "C2.interpret_brief", inp)
        turn.llm_calls += calls
        _note_slow(turn, used_fallback)
        assert isinstance(out, C2Output)
        return out


class Responder:
    """C3: questions, chit-chat, brainstorming; answers only from the knowledge base."""

    def __init__(self, llm: Llm, banks: QuestionBanks) -> None:
        self.llm = llm
        self.banks = banks

    async def respond(
        self, turn: Turn, intent: Intent, text: str, *, stuck: bool = False
    ) -> C3Output:
        inp = C3Input(
            message=text,
            intent=intent,
            language=turn.lang,
            mode=turn.mode,
            slots=slot_briefs(turn),
            recent_turns=turn.prompt_turns(),
            summary=turn.state.summary,
            knowledge=self.banks.knowledge(turn.lang),
            pending_card=pending_lite(turn.state.pending_card),
            stuck=stuck,
            off_topic_streak=turn.state.off_topic_streak,
        )
        out, used_fallback, calls = await self.llm.run(turn.ctx, "C3.respond", inp)
        turn.llm_calls += calls
        _note_slow(turn, used_fallback)
        assert isinstance(out, C3Output)
        return out


class ChangeInterpreter:
    """C14: a change request or a gate comment as patch operations on slots."""

    def __init__(self, llm: Llm, understander: Understander) -> None:
        self.llm = llm
        self._understander = understander

    async def interpret(
        self,
        turn: Turn,
        text: str,
        *,
        source: str = "text",
        gate_open: GateKind | None = None,
        selected_screen_id: str | None = None,
    ) -> C14Output:
        inp = C14Input.model_validate(
            {
                "message": text,
                "language": turn.lang,
                "state": turn.req.session_state,
                "gate_open": gate_open if gate_open is not None else turn.req.gate_open,
                "slots": slot_briefs(turn),
                "selected_screen_id": selected_screen_id,
                "slot_catalog": self._understander.catalog(turn),
                "source": source,
            }
        )
        out, used_fallback, calls = await self.llm.run(turn.ctx, "C14.interpret_change", inp)
        turn.llm_calls += calls
        _note_slow(turn, used_fallback)
        assert isinstance(out, C14Output)
        return out


class CardWriter:
    """C5: write a card only when the bank has none for a slot."""

    def __init__(self, llm: Llm, vocab: Vocab, catalogs: Catalogs) -> None:
        self.llm = llm
        self.vocab = vocab
        self._defs = {s.slot_id: s for s in catalogs.slots}

    async def write(self, turn: Turn, slot_id: str) -> Card:
        sd = self._defs[slot_id]
        archetype = turn.store.text_value("idea.archetype") or "custom"
        allowed = self.vocab.allowed(slot_id, archetype)
        kind = sd.value_kind
        if kind == "list" and allowed:
            card_type: Any = "multi"
        elif kind in ("choice", "bool"):
            card_type = "choice3"
        else:
            card_type = "text"
        hints = ["true", "false"] if kind == "bool" else list(allowed or [])[:8]
        inp = C5Input(
            slot=slot_id,  # type: ignore[arg-type]
            slot_hint=sd.default_reason,
            archetype=archetype,
            language=turn.lang,
            mode=turn.mode,
            card_type=card_type,
            slots=[b for b in slot_briefs(turn) if b.slot_id in ("idea.summary", "idea.archetype")],
            options_hint=hints,
        )
        out, used_fallback, calls = await self.llm.run(turn.ctx, "C5.write_card", inp)
        turn.llm_calls += calls
        _note_slow(turn, used_fallback)
        assert isinstance(out, C5Output)
        return self._to_card(slot_id, card_type, out, sd.default)

    @staticmethod
    def _to_card(slot_id: str, card_type: str, out: C5Output, default: Any) -> Card:
        options: list[CardOption] = []
        for i, draft in enumerate(out.options):
            value: Any = (
                draft.value_list
                if draft.value_list is not None
                else (draft.value_bool if draft.value_bool is not None else draft.value_text)
            )
            option_id = (
                str(draft.value_list[0])
                if card_type == "multi" and draft.value_list
                else chr(ord("A") + i)
            )
            options.append(
                CardOption(
                    id=option_id,
                    label=draft.label,
                    description=draft.description,
                    recommended=draft.recommended,
                    sets={}
                    if card_type == "multi"
                    else ({slot_id: value} if value is not None else {}),
                )
            )
        if options and not any(o.recommended for o in options) and card_type != "multi":
            options[0] = options[0].model_copy(update={"recommended": True})
        final_type: Any = card_type if options else "text"
        recommended = next((o.id for o in options if o.recommended), None)
        return Card(
            id=f"Q-gen-{slot_id.replace('.', '-').replace('_', '-')}",
            slot=slot_id,  # type: ignore[arg-type]
            type=final_type,
            text=out.text,
            options=options,
            other=OtherInput(enabled=True, placeholder=out.other_placeholder),
            skip=SkipSpec(enabled=True, assumes=recommended),
            ask_if="true",
            help=out.help,
        )


def _note_slow(turn: Turn, used_fallback: bool) -> None:
    """Remember that a fallback was used this turn (the engine emits one friendly notice)."""
    if used_fallback:
        turn.fallback_used = True
