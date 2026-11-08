"""The Brief (spec 2.3): the single output of the Conversation Engine, shown at Gate 1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from mde_contracts.common import Frozen, Mode
from mde_contracts.slots import SlotId
from mde_core.hashing import hash_model

BriefRowKey = Literal["building", "users", "features", "look", "login", "name_logo"]
BriefStatus = Literal["draft", "approved", "superseded"]


class BriefFeature(Frozen):
    id: str
    label: str
    on: bool = True


class BriefRow(Frozen):
    key: BriefRowKey
    title: str
    text: str  # plain-language content of the row
    slot_ids: list[SlotId]  # tap-to-edit opens the card of the first slot
    features: list[BriefFeature] = []  # for the "features" row only
    swatch: list[str] = []  # hex colours for the "look" row


class AssumedItem(Frozen):
    """Something we assumed instead of asking. The user can toggle or edit each one."""

    slot_id: SlotId
    label: str
    value_text: str
    reason: str  # one-line reason shown to the user
    included: bool = True


class Brief(Frozen):
    version: int
    hash: str = ""  # sha256 of the content (``compute_hash``); excludes hash, status, created_at
    status: BriefStatus = "draft"
    mode: Mode = "guided"
    language: str = "en"
    app_summary: str  # C11: one sentence
    rows: list[BriefRow]
    assumptions: list[AssumedItem] = []
    created_at: datetime | None = None

    def compute_hash(self) -> str:
        return hash_model(self, exclude={"hash", "status", "created_at"})

    def with_hash(self) -> Brief:
        return self.model_copy(update={"hash": self.compute_hash()})

    def row(self, key: BriefRowKey) -> BriefRow | None:
        return next((r for r in self.rows if r.key == key), None)
