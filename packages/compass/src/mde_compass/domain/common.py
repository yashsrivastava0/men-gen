"""Shared building blocks of every Brief item: tier, provenance, status, history.

Tier rules (code, tested; ADR 0013):
* ``Locked`` only if the provenance holds at least one *evidence* entry: type user_tap, user_text,
  voice, document or org_profile, with a non-empty ``ref`` (a turn id or ``SRC-###`` + pointer).
* ``assumed``, ``pattern`` and ``model_knowledge`` provenance can only yield ``Default`` (value present)
  or ``Open`` (no value). Research and assumptions never produce Locked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Tier = Literal["Locked", "Default", "Open"]
ProvenanceType = Literal[
    "user_tap",
    "user_text",
    "voice",
    "document",
    "assumed",
    "pattern",
    "model_knowledge",
    "org_profile",
]
Status = Literal["empty", "assumed", "volunteered", "confirmed", "disputed"]
"""Slot states (prompt 5.1): empty, assumed, volunteered, confirmed, disputed."""

EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"user_tap", "user_text", "voice", "document", "org_profile"}
)
Basis = Literal["your_document", "typical", "suggestion", "your_words"]
"""Basis labels users see on options and badges."""


class Strict(BaseModel):
    """Base for domain models: unknown fields are rejected so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Provenance(Strict):
    """Where a value came from. ``ref`` is a turn id (``evt_...``) or ``SRC-###`` plus a pointer."""

    type: ProvenanceType
    ref: str = ""
    quote: str = ""
    date: datetime | None = None
    contributor_id: str | None = None

    def is_evidence(self) -> bool:
        return self.type in EVIDENCE_TYPES and bool(self.ref.strip())


class HistoryEntry(Strict):
    """A previous value of an item (undo and "where did this come from")."""

    value: JsonValue = None
    status: Status = "empty"
    turn_id: str | None = None
    at: datetime | None = None
    reason: str = ""


class ItemMeta(Strict):
    """Every Brief item (not only top-level keys) carries these five."""

    tier: Tier = "Open"
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Status = "empty"
    history: list[HistoryEntry] = Field(default_factory=list)


def has_evidence(provenance: list[Provenance]) -> bool:
    return any(p.is_evidence() for p in provenance)


def max_tier(*, has_value: bool, provenance: list[Provenance]) -> Tier:
    """The highest tier the evidence allows (never above it)."""
    if not has_value:
        return "Open"
    return "Locked" if has_evidence(provenance) else "Default"


def tier_errors(meta: ItemMeta, *, has_value: bool, where: str = "") -> list[str]:
    """Validator: a Locked item without evidence is rejected."""
    allowed = max_tier(has_value=has_value, provenance=meta.provenance)
    order = {"Open": 0, "Default": 1, "Locked": 2}
    if order[meta.tier] > order[allowed]:
        return [f"{where or 'item'}: tier {meta.tier} needs evidence (allowed: {allowed})"]
    return []


class BriefItem(ItemMeta):
    """One slot-valued entry of a dict section (idea, context, audience, experience, ...)."""

    value: JsonValue = None

    def has_value(self) -> bool:
        v = self.value
        return v is not None and v != "" and v != [] and v != {}
