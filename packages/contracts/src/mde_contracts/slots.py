"""Slots (spec 3.3): what the interview fills. Levels, states and sources are fixed vocabulary;
the per-slot table (level by mode, default, preconditions) is *content* (``SlotDef`` in YAML).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from mde_contracts.common import Frozen, Mode

SlotId = Literal[
    # idea
    "idea.summary",
    "idea.archetype",
    "scope.apps",
    "scope.features",
    # users
    "users.roles",
    "auth.method",
    # look
    "design.direction",
    "brand.name",
    "brand.assets",
    "design.accent",
    "design.mode",
    "design.nav_style",
    # money
    "pay.model",
    "pay.provider",
    "data.source",
    # behavior
    "behavior.offline",
    "behavior.notifications",
    "behavior.analytics",
    "locale",
    "a11y",
    # compliance
    "compliance.flags",
    # enterprise
    "auth.sso",
    "integrations.list",
    "data.classification",
    "regions",
    "release.channel",
    "approvers",
]
SLOT_IDS: tuple[str, ...] = get_args(SlotId)

SlotLevel = Literal["must", "should", "may"]
SlotState = Literal["empty", "assumed", "volunteered", "confirmed", "disputed"]
SlotSource = Literal["tap", "text", "upload", "research", "org_profile", "memory", "default"]
SlotGroup = Literal["idea", "users", "look", "money", "behavior", "compliance", "enterprise"]
SlotValueKind = Literal["text", "bool", "choice", "list"]

# What a slot can hold. Persisted as JSONB. (LLM outputs never use this type: see calls.SlotUpdate.)
SlotData = str | int | float | bool | list[str] | None


class SlotRecord(Frozen):
    """One slot value with provenance (rules in spec 3.3 are enforced by ``mde_discovery``)."""

    slot_id: SlotId
    value: SlotData = None
    state: SlotState = "empty"
    source: SlotSource | None = None
    evidence: str | None = None
    updated_at: datetime | None = None


class SlotDef(Frozen):
    """Static definition of a slot (content: ``slots.yaml``)."""

    slot_id: SlotId
    group: SlotGroup
    level: dict[Mode, SlotLevel]
    value_kind: SlotValueKind = "text"
    default: SlotData = None
    default_reason: str = ""
    depends_on: list[SlotId] = []
    ask_if: str | None = None  # safe expression, see docs/specs/question-cards.md
    must_confirm_in: list[Mode] = []  # cannot pass Gate 1 as an assumption in these modes
    order: int = (
        100  # tie-break inside the dependency order (type, apps, features, login, look, pay)
    )
