"""Wire models of ``cmp.understand_turn`` (strict-schema-safe: all fields required, no open objects).

Values travel as ``value_json`` strings that code parses and validates against the slot's ``value_schema``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_compass.engine.ports import Intent


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


class UnderstandInput(Wire):
    text: str
    input_mode: Literal["text", "voice"]
    language: str
    pending_card: PendingCardWire | None
    current_slots: list[SlotCurrentWire]
    slot_hints: list[SlotHintWire]
    recent_turns: list[TurnWire]


class SlotUpdateWire(Wire):
    slot_id: str
    value_json: str
    quote: str
    confidence: float
    basis: Literal["user_text", "voice", "assumed", "model_knowledge"]
    remove: bool


class UnderstandOutput(Wire):
    intent: Intent
    slot_updates: list[SlotUpdateWire]
    references: list[str]
    language: str
    safety_flags: list[str]
    ambiguous: list[str]
    confidence: float
