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
# ``closing`` is the one reserved last question of an interview (ADR 0016); it is stored with mode ``replan``
QuestionOrigin = Literal["planner", "followup", "fallback", "frame", "closing"]
"""Where a question came from: the planner, a follow-up, a flagged fallback, or the legacy frame path."""
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
    description: str = ""  # planner questions: a short line under the label


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
    # --- planned questions (build prompt 02): all optional, so every older card still validates ---------
    batch_id: str | None = None
    group_id: str | None = None
    need_ids: list[str] = Field(
        default_factory=list
    )  # the needs it serves: a slot id or "domain:<topic>"
    intent: str = ""  # what we want to learn, in plain words
    unlocks: list[str] = Field(
        default_factory=list
    )  # downstream decisions it feeds (Research query, roles...)
    answer_spec: str = ""  # what a good answer contains
    followup_hint: str = ""  # what to ask if the answer is vague
    examples: list[str] = Field(default_factory=list)  # placeholder hints for text questions
    parent_card_id: str | None = None  # a follow-up points at the question it refines
    depth: int = Field(default=0, ge=0)  # follow-up depth (0 = a base question)
    origin: QuestionOrigin = "frame"
    priority: int = Field(
        default=3, ge=1, le=5
    )  # the planner's own rank, 1 (most important) to 5: batch truncation keeps the lowest numbers
    rephrased: int = Field(default=0, ge=0)  # times it was rewritten after "I do not understand"

    def recommended_option(self) -> CardOption | None:
        for option in self.options:
            if option.recommended:
                return option
        return None
