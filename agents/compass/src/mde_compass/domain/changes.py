"""Patch operations and change sets (prompt 6.10, ADR 0013).

A change set is validated on a copy, applied as ``version + 1`` and is revertible by applying its
``inverse_ops`` as a *new* version; history is never rewritten.

Path grammar (one rule, used by the patch engine, the view model and the UI):
* dict sections are addressed ``<section>.<key>``: ``idea.summary``, ``context.geo``, ``experience.tone_words``;
* list sections address a record by id: ``scope.FTR-004`` (whole record) and ``scope.FTR-004.priority`` (a field);
* ``op``: ``set`` replaces a value or field, ``add`` appends a record to a list section (value is the record),
  ``remove`` deletes a record (leaves a tombstone), ``clear`` empties a value (explicit negation).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mde_compass.domain.common import Provenance, Strict

OpName = Literal["set", "add", "remove", "clear"]
ChangeSource = Literal[
    "ui_edit",
    "feedback",
    "chat",
    "assumption",
    "lookup",
    "undo",
    "engine",
    "approve_all",
    "structure",
]
FeedbackReason = Literal[
    "not_what_i_meant",
    "too_much",
    "something_missing",
    "wrong_tone",
    "dont_want_assumption",
    "other",
]


class PatchOp(Strict):
    op: OpName
    path: str
    value: JsonValue = None
    old_value: JsonValue = None  # filled when applied, so the inverse is exact
    provenance: list[Provenance] = Field(default_factory=list)


class ChangeSet(Strict):
    id: str  # CHG-###
    base_version: int
    new_version: int
    ops: list[PatchOp]
    inverse_ops: list[PatchOp] = Field(default_factory=list)
    source: ChangeSource = "engine"
    reason: str = ""
    summary: str = ""  # plain words: "Updated: Features › Group ordering moved to Later"
    turn_id: str | None = None
    voided_sections: list[str] = Field(default_factory=list)
    reseeded_research: bool = False
    undoes: str | None = None  # the CHG- id this change set reverts
