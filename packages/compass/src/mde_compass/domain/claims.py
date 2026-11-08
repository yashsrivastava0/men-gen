"""Sources, chunks and claims (document intake, prompt 6.5). Models are defined now so the store and the
engine have one vocabulary; the parsers themselves arrive with the intake phase."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_compass.domain.common import Strict

StatementType = Literal[
    "requirement",
    "constraint",
    "business_context",
    "goal",
    "user_or_role",
    "rule_or_number",
    "glossary_term",
    "example",
    "open_issue",
    "preference",
    "exclusion",
]
ClaimStatus = Literal["used", "duplicate", "conflicting", "ignored", "needs_confirming"]
SourceKind = Literal[
    "pdf", "docx", "xlsx", "pptx", "csv", "text", "markdown", "eml", "image", "url"
]


class Chunk(Strict):
    id: str
    source_id: str
    index: int
    text: str
    pointer: str = ""  # "page 3", "sheet Roles!A1:D9", "heading: Payments"
    unreadable: bool = False


class Statement(Strict):
    """What ``cmp.read_chunk`` returns: a typed statement with a source pointer and a verbatim quote."""

    type: StatementType
    text: str
    quote: str
    pointer: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Claim(Strict):
    id: str
    source_id: str
    statement: Statement
    status: ClaimStatus = "needs_confirming"
    slot_hint: str | None = None
    conflicts_with: list[str] = Field(default_factory=list)


class SourceInfo(Strict):
    id: str  # SRC-###
    kind: SourceKind
    name: str
    sha256: str
    bytes: int = 0
    pages: int = 0
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: list[str] = Field(
        default_factory=list
    )  # e.g. "unreadable_page: 4", "password protected"
