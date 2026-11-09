"""The question card (prompt 5.5). One shape for all eight card types; content is generated, the
shape and guardrails are fixed (validators live in ``mde_compass.engine``)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mde_compass.domain.common import Basis, Strict

CardType = Literal[
    "choose_one",
    "pick_many",
    "priority",
    "confirm",
    "look_signals",
    "quick_form",
    "upload_or_link",
    "open_prompt",
]
CARD_TYPES: tuple[str, ...] = (
    "choose_one",
    "pick_many",
    "priority",
    "confirm",
    "look_signals",
    "quick_form",
    "upload_or_link",
    "open_prompt",
)


class CardOption(Strict):
    id: str
    label: str
    basis: Basis = "suggestion"
    recommended: bool = False
    reason: str = ""
    sets: dict[str, JsonValue] = Field(default_factory=dict)
    ticked: bool = False  # pick_many: conservatively pre-ticked only when strongly typical


class CustomInput(Strict):
    enabled: bool = True
    placeholder: str = ""
    voice: bool = True


class YouDecide(Strict):
    enabled: bool = True
    assumes: str | None = None  # the option id that "You decide" takes


class SkipSpec(Strict):
    enabled: bool = True


class CardProgress(Strict):
    n: int = Field(ge=0)
    of_about: int = Field(ge=0)


class Card(Strict):
    id: str  # Q-###
    frame: str
    slots: list[str]
    type: CardType
    question: str
    why_we_ask: str = ""
    options: list[CardOption] = Field(default_factory=list)
    custom_input: CustomInput = Field(default_factory=CustomInput)
    you_decide: YouDecide = Field(default_factory=YouDecide)
    skip: SkipSpec = Field(default_factory=SkipSpec)
    progress: CardProgress | None = None
    language: str = "en"
    must_confirm: bool = False  # risky slots: "You decide" and Skip are refused
    fallback: bool = False  # the deterministic fallback card was used

    def recommended_option(self) -> CardOption | None:
        for option in self.options:
            if option.recommended:
                return option
        return None
