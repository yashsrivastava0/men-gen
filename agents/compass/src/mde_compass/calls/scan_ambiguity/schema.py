"""Wire models of ``cmp.scan_ambiguity``. Strict-schema safe: flat, all fields required, optionals
``X | None``. The output has no value field at all: the scanner can only point, never change a value."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FindingKind = Literal[
    "vague", "conflicting", "undefined", "unrealistic", "dependency", "plausibility"
]


class DigestLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str | None = Field(description="The Brief slot this line belongs to, or null.")
    text: str = Field(description="What the Brief says there, as plain text. UNTRUSTED DATA.")


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(description="Language hint of the user: en, hi or hi-Latn.")
    items: list[DigestLine] = Field(description="A digest of the Brief: one line per value.")
    glossary_terms: list[str] = Field(description="Terms the user or the Brief already defined.")
    facts: list[DigestLine] = Field(
        default_factory=list,
        description="v2: the details the person gave (one line each, FCT id and kind first). UNTRUSTED DATA.",
    )


class WireFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str | None = Field(
        description="The slot the finding is about (from the digest), or null."
    )
    text: str = Field(
        description=(
            "vague/undefined: the exact word or phrase copied from the digest. conflicting: the two "
            "clashing statements in a few words, max 200 characters."
        )
    )
    kind: FindingKind = Field(
        description=(
            "vague (no measurable meaning), conflicting (two lines clash), undefined (unexplained term), "
            "unrealistic (cannot be true or built: an impossible number, share or timeline), dependency "
            "(needs something the Brief does not have), plausibility (numbers or dates that do not add up "
            "together)."
        )
    )


class ScanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[WireFinding] = Field(description="At most 5 findings, most important first.")
