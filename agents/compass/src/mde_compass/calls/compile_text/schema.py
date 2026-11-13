"""Wire models of ``cmp.compile_text`` (strict-schema-safe: flat, all fields required, no open objects)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_ITEMS = 40
MAX_SUMMARY_CHARS = 160
MAX_PITCH_CHARS = 400
MAX_DESCRIPTION_CHARS = 240
MAX_DETAILS = 12
DETAIL_CHARS = 160


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotText(Wire):
    """One slot as plain text: what the user said (or what was assumed), never a structure."""

    slot_id: str
    label: str
    value: str
    state: str  # empty | assumed | volunteered | confirmed | disputed


class ItemRef(Wire):
    """A Brief row that needs a plain-language description (app, role or feature)."""

    path: str  # ChangeSet path such as "scope.FTR-004"
    name: str
    intent: str


class CompileTextInput(Wire):
    language: str
    slots: list[SlotText]
    app_count: int
    role_count: int
    feature_count: int
    items: list[ItemRef]  # at most MAX_ITEMS
    # v2 (ADR 0016): the rules, numbers and duties the person gave, so the pitch can say what matters most and every
    # figure it mentions is grounded in the input (at most MAX_DETAILS)
    details: list[str] = Field(default_factory=list)


class ItemText(Wire):
    item_id: str
    text: str


class CompileTextOutput(Wire):
    summary: str  # one line, at most 160 characters
    pitch: str  # at most 400 characters
    descriptions: list[ItemText]
