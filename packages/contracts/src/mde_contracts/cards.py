"""Question cards (spec 3.4). Cards are pre-written content; C5 writes one only when none exists.

Answer semantics (normative):
- ``choice3`` / ``visual3`` / ``confirm``: exactly one option; its ``sets`` map is applied
  (slot id -> value), source ``tap``.
- ``multi``: any subset of options; the card's ``slot`` receives the list of selected option ids
  (``sets`` is ignored); options with ``recommended`` are pre-ticked.
- ``text`` / ``upload``: the typed text or upload reference goes to ``card.slot``.
- "Something else" (``other``): the typed text is sent through the understander (C1) *and* answers
  the card; "You decide" / skip picks the recommended option (or ``skip.assumes``) as ``assumed``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from mde_contracts.common import Frozen, Mode
from mde_contracts.slots import SlotData, SlotId

CardType = Literal["choice3", "multi", "visual3", "text", "upload", "confirm"]


class CardOption(Frozen):
    id: str
    label: str
    description: str | None = None
    preview: str | None = None  # theme token reference ("theme:clean-bright") or CDN url
    recommended: bool = False
    sets: dict[str, SlotData] = Field(default_factory=dict)  # slot id -> value


class OtherInput(Frozen):
    enabled: bool = True
    placeholder: str | None = None


class SkipSpec(Frozen):
    enabled: bool = True
    assumes: str | None = None  # option id; default = the recommended option


class CardI18n(Frozen):
    """Localised copy for one language (``hi`` Hindi, ``hi-Latn`` Hinglish, ...)."""

    text: str
    options: dict[str, str] = Field(default_factory=dict)  # option id -> label
    other_placeholder: str | None = None
    help: str | None = None


class Card(Frozen):
    id: str  # "Q-look-01"
    slot: SlotId
    type: CardType
    text: str
    options: list[CardOption] = []
    other: OtherInput = Field(default_factory=OtherInput)
    skip: SkipSpec = Field(default_factory=SkipSpec)
    ask_if: str | None = None  # safe expression over slots; never eval'd
    help: str | None = None
    archetypes: list[str] = Field(default_factory=list)  # empty = all archetypes
    modes: list[Mode] = []  # empty = both modes
    kind: Literal["standard", "help", "compliance", "resolution", "inspiration", "nudge"] = (
        "standard"
    )
    version: int = 1
    i18n: dict[str, CardI18n] = Field(default_factory=dict)


class CardProgress(Frozen):
    asked: int
    cap: int


class CardInstance(Frozen):
    """A card as shown to the user in one session."""

    instance_id: str  # crd_...
    card: Card
    shown_at: datetime | None = None
    language: str = "en"
    progress: CardProgress | None = None
    generated: bool = False  # True when written by C5 (not from the bank)
