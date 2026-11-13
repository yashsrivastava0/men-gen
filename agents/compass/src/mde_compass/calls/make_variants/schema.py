"""Wire models of ``cmp.make_variants`` (strict-schema safe: flat, all fields required, no open objects).

Values travel as ``value_json`` (JSON text) and are parsed and validated by code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VARIANT_COUNT = 3
MAX_LABEL_WORDS = 8
MAX_REASON_CHARS = 200
Basis = Literal["your_document", "typical", "suggestion", "your_words"]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Choice(_Wire):
    """A neutral catalog option for the slot (label in the user's language and the value it sets)."""

    label: str
    value_json: str = Field(description="The value this option sets, as JSON text.")


class ContextSlot(_Wire):
    slot_id: str
    label: str
    value: str = Field(description="The value so far as short plain text.")


class FamilyHint(_Wire):
    """Typical items from the capability family data (the only source for basis ``typical``)."""

    family: str
    kind: Literal["features", "roles", "integrations", "business_models"]
    items: list[str]


class VariantsIn(_Wire):
    path: str = Field(description="Path of the disliked item, for example idea.business_model.")
    current_value_json: str = Field(description="The current value as JSON text (data).")
    reason: str = Field(
        default="", description="Why the person dislikes it: a chip or free text (data)."
    )
    language: str = Field(default="en", description="en, hi or hi-Latn.")
    slot_label: str = Field(
        default="", description="Plain label of the slot, or empty for other paths."
    )
    value_kind: str = Field(
        default="",
        description="Slot kind: text, enum, enum_list, text_list, object_list, object, bool, geo, locales, "
        "record (a list record) or empty when the path is not a slot.",
    )
    options: list[str] = Field(
        default_factory=list, description="Valid values when the kind is an enum."
    )
    name_field: str = Field(
        default="", description="For a list record: the field that holds its name, else empty."
    )
    choices: list[Choice] = Field(
        default_factory=list, description="Neutral catalog options for the slot."
    )
    context: list[ContextSlot] = Field(
        default_factory=list, description="Filled slots, at most 12."
    )
    families: list[FamilyHint] = Field(
        default_factory=list, description="Typical items, at most 2 families."
    )


class VariantOut(_Wire):
    label: str = Field(
        description="Short name of the alternative, at most 8 words, in the person's language."
    )
    value_json: str = Field(
        description="The value it would set, as JSON text valid for the slot kind."
    )
    reason: str = Field(description="One plain sentence on what this alternative means for them.")
    basis: Basis = Field(
        description="typical only if copied from the families items; your_words only if the person said it; "
        "otherwise suggestion."
    )


class VariantsOut(_Wire):
    variants: list[VariantOut] = Field(
        description="Exactly three distinct alternatives, none equal to the current value."
    )
