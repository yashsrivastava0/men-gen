"""The structure of the app (ADR 0017): modules, screens, flows and entities, at intent level.

These records are what the AI drafts from the idea, the answers, documents and domain knowledge and what the person
approves at Gate 1. They are deliberately small: a title, a purpose, who uses it, what it serves, what data it touches.
Routes, the navigation tree, API operations, lifecycle transitions and per-screen sections, states and copy belong to
the later Blueprint and Spec stages, not here.

Ids use the Spec Graph prefixes (MOD, SCR, FLW, ENT; architecture-v3 doc 04 section 1) so a later stage inherits them.
Every record carries ``ItemMeta`` (tier, provenance, confidence, status, history) and a real ``decision`` field:
``proposed`` (drafted, not reviewed) or ``accepted`` (the person looked at it). Accepting sets ``decision`` and does
not create evidence; a typed edit does (``user_text``), which is what lets a record be ``Locked``. Accepted and Locked
records are PINNED: a regeneration keeps them.

Closed vocabularies (screen patterns, field types, access levels) live in ``catalog/structure_patterns.yaml`` and are
checked by ``engine/structure_checks`` rather than typed here, so the data file can grow without a schema change.
The four lists are optional Brief sections: an empty one is left out of the hash, of the section hashes and of the dump
(``domain/brief.py``), so a Brief written before this existed is unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_compass.domain.common import ItemMeta, Strict

Decision = Literal["proposed", "accepted"]
AccessLevel = Literal["none", "view", "create", "edit", "manage"]
EntityUseKind = Literal["view", "create", "edit"]

# what a role can do with an entity, from least to most; a screen that creates or edits needs at least that much
ACCESS_ORDER: tuple[AccessLevel, ...] = ("none", "view", "create", "edit", "manage")
STRUCTURE_SECTIONS: tuple[str, ...] = ("modules", "screens", "flows", "entities")
ID_PREFIX_BY_STRUCTURE_SECTION: dict[str, str] = {
    "modules": "MOD",
    "screens": "SCR",
    "flows": "FLW",
    "entities": "ENT",
}


def access_allows(level: AccessLevel, use: EntityUseKind) -> bool:
    """Whether an access level covers what a screen does with an entity (view, create or edit)."""
    return ACCESS_ORDER.index(level) >= ACCESS_ORDER.index(use)


class ModuleRecord(ItemMeta):
    """An area of the app: the unit the person reviews and the unit a downstream call is sliced by."""

    id: str  # MOD-###
    name: str
    purpose: str = ""
    apps: list[str] = Field(default_factory=list)  # APP-###
    features: list[str] = Field(default_factory=list)  # FTR-### the module delivers
    roles: list[str] = Field(default_factory=list)  # ROL-### who works in it
    entry: str = ""  # SCR-### the module opens on
    decision: Decision = "proposed"


class ScreenEntityUse(Strict):
    """What a screen does with an entity: show it, create it or edit it."""

    entity: str  # ENT-###
    use: EntityUseKind = "view"


class ScreenRecord(ItemMeta):
    id: str  # SCR-###
    name: str
    purpose: str = ""
    module: str = ""  # MOD-###
    apps: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)  # who can open it
    serves: list[str] = Field(default_factory=list)  # FTR-### it is part of (traceability)
    entities: list[ScreenEntityUse] = Field(default_factory=list)
    pattern: str = ""  # one of structure_patterns.yaml: list, detail, form, dashboard, ...
    decision: Decision = "proposed"


class FlowStep(Strict):
    screen: str  # SCR-###
    action: str = ""  # what the person does there, in a few words


class FlowRecord(ItemMeta):
    """A journey a person goes through to get something done, as an ordered list of screens."""

    id: str  # FLW-###
    name: str
    goal: str = ""
    role: str = ""  # ROL-### who goes through it
    steps: list[FlowStep] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)  # FTR-###
    alternates: list[str] = Field(default_factory=list)  # other paths and failure paths, in words
    acceptance: list[str] = Field(
        default_factory=list
    )  # what must be true for the journey to count as working
    decision: Decision = "proposed"


class EntityField(Strict):
    key: str  # ascii snake_case identifier, stable whatever language the interview used
    name: str = ""  # display name, in the interview language
    type: str = "text"  # one of structure_patterns.yaml: text, number, money, date, ...
    required: bool = False
    ref: str = ""  # ENT-### when the type is ref
    values: list[str] = Field(default_factory=list)  # the allowed values when the type is enum


class EntityRecord(ItemMeta):
    """A thing the app keeps track of, with its fields and who may do what with it."""

    id: str  # ENT-###
    key: str = ""  # ascii snake_case identifier
    name: str
    purpose: str = ""
    module: str = (
        ""  # MOD-### whose call writes its fields: the owner, so a slice by module can carry it
    )
    fields: list[EntityField] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)  # a short list when the thing has a status
    access: dict[str, AccessLevel] = Field(default_factory=dict)  # ROL-### -> what that role may do
    decision: Decision = "proposed"


STRUCTURE_RECORDS: dict[str, type[ItemMeta]] = {
    "modules": ModuleRecord,
    "screens": ScreenRecord,
    "flows": FlowRecord,
    "entities": EntityRecord,
}
