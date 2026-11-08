"""Wire models of ``cmp.write_card`` (strict-schema-safe). Option values travel as ``sets_json``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameWire(Wire):
    id: str
    purpose: str
    guidance: str
    card_type: str
    option_count: int | None  # null means a checklist
    max_question_words: int
    max_option_words: int


class SlotDefWire(Wire):
    slot_id: str
    label: str
    value_kind: str
    options: list[str]
    fields: list[str]


class ProfileWire(Wire):
    expertise: str
    formality: str
    language: str


class KnownSlotWire(Wire):
    slot_id: str
    value_json: str


class WriteCardInput(Wire):
    frame: FrameWire
    slots: list[SlotDefWire]
    option_source: str
    hints: list[str]
    evidence: list[str]
    must_confirm: bool
    language: str
    avoid_labels: list[str]
    profile: ProfileWire
    known_slots: list[KnownSlotWire]
    question_number: int


class OptionWire(Wire):
    id: str
    label: str
    basis: Literal["your_document", "typical", "suggestion", "your_words"]
    recommended: bool
    reason: str
    sets_json: str  # JSON object: slot_id -> value this option sets ("{}" for none)
    ticked: bool


class WriteCardOutput(Wire):
    question: str
    why_we_ask: str
    options: list[OptionWire]
    placeholder: str
