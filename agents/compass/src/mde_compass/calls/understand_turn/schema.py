"""Wire models of ``cmp.understand_turn`` (strict-schema-safe: all fields required, no open objects).

Values travel as ``value_json`` strings that code parses and validates against the slot's ``value_schema``.

Version 2 (build prompt 02, ADR 0015) adds, all flat and all required (empty when absent):

* input: the focus question and its need ids, the needs table with what is still missing, and the fact digest;
* output: atomic ``facts`` with the user's verbatim quote and the aspects of the listed needs they answer,
  ``flags``, ``glossary_terms`` and ``contradicts``.

The call reports; code decides (need status, follow-ups, what is stored). It never asks and never chooses what
happens next.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_compass.domain.interview import EffectMode, FactType
from mde_compass.engine.ports import Intent, UnderstandFlag


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PendingOptionWire(Wire):
    id: str
    label: str
    sets_json: str  # JSON object slot_id -> value this option sets ("{}" when none)


class PendingCardWire(Wire):
    card_id: str
    frame: str
    slots: list[str]
    options: list[PendingOptionWire]


class SlotCurrentWire(Wire):
    slot_id: str
    value_json: str
    state: str


class SlotHintWire(Wire):
    slot_id: str
    label: str
    value_kind: str
    options: list[str]
    fields: list[str]


class TurnWire(Wire):
    role: Literal["user", "assistant"]
    text: str


class NeedWire(Wire):
    """One need the message may help answer: what a sufficient answer is, and what is still missing."""

    need_id: str
    label: str
    sufficient: str  # the answer_spec text
    aspects: list[str]  # the named parts an answer can cover
    state: Literal["satisfied", "partial", "missing", "assumed"]
    missing_aspects: list[str]


class UnderstandInput(Wire):
    text: str
    input_mode: Literal["text", "voice"]
    language: str
    pending_card: PendingCardWire | None
    current_slots: list[SlotCurrentWire]
    slot_hints: list[SlotHintWire]
    recent_turns: list[TurnWire]
    # --- v2: the question this message answers (all empty when it answers none) -------------------------
    focus_card_id: str
    focus_question: str
    focus_intent: str  # what the question wanted to learn
    focus_need_ids: list[str]  # needs (or "domain:<topic>") the question serves
    focus_selected_labels: list[str]  # option labels the person tapped (empty for typed text)
    needs: list[NeedWire]  # open needs first; the focus needs are always listed
    fact_digest: str  # one line per active fact: FCT-014 [rule|pricing] statement (from Q-03)


class SlotUpdateWire(Wire):
    slot_id: str
    value_json: str
    quote: str
    confidence: float
    basis: Literal["user_text", "voice", "assumed", "model_knowledge"]
    remove: bool


class EffectWire(Wire):
    """What a fact does to one slot (the same value a slot update would carry)."""

    slot_id: str
    value_json: str
    mode: EffectMode


class FactWire(Wire):
    """One atomic statement, normalized to the person's vocabulary, with their verbatim words."""

    type: FactType
    statement: str
    quote: str  # copied exactly from the message (for a tap, the option label)
    topics: list[str]
    entities: list[str]
    consumers: list[str]
    confidence: float
    aspects: list[str]  # "need_id:aspect" this statement answers; "need_id:*" for a complete answer
    effects: list[EffectWire]
    corrects: str  # "" or the id (FCT-###) of an earlier fact this clearly corrects


class GlossaryTermWire(Wire):
    term: str
    synonyms: list[str]


class UnderstandOutput(Wire):
    intent: Intent
    slot_updates: list[SlotUpdateWire]
    references: list[str]
    language: str
    safety_flags: list[str]
    ambiguous: list[str]
    confidence: float
    # --- v2 ------------------------------------------------------------------------------------------------
    facts: list[FactWire]
    flags: list[UnderstandFlag]
    glossary_terms: list[GlossaryTermWire]
    contradicts: list[
        str
    ]  # ids (FCT-###) of earlier facts something in this message conflicts with
