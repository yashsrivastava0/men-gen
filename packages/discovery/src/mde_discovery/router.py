"""IntentRouter: deterministic fast paths that run BEFORE any LLM call (spec 3.1, brief 8.2).

A typed message that is exactly one of the control phrases (greeting, "you decide", "finish with
smart defaults", stop, pause, start over, "none of these") or a starter chip costs no LLM call. The
phrase tables are data (``mde_prompts`` lexicon, English, Hindi and Hinglish); longer free text goes
to C1. Taps (option selections, buttons) never reach this class: they are handled by event type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mde_content.questions import QuestionBanks
from mde_contracts.cards import CardInstance
from mde_contracts.content import StarterChip
from mde_prompts.calls.c1_understand import lexicon as lx

RouteKind = Literal[
    "greeting",
    "you_decide",
    "finish_with_defaults",
    "stop",
    "pause",
    "start_over",
    "none_of_these",
    "chip",
]
SHORT = 6  # "finish with smart defaults please" style phrases may carry a few extra words
CHIP_LANGS = ("en", "hi", "hi-Latn")


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    chip: StarterChip | None = None


class IntentRouter:
    def __init__(self, banks: QuestionBanks) -> None:
        self._chips: dict[str, StarterChip] = {}
        for archetype in (None, *banks.archetypes):
            for lang in CHIP_LANGS:
                for chip in banks.chips(archetype, lang):
                    for text in (chip.label, chip.prefill or ""):
                        norm = lx.normalize(text)
                        if norm:
                            self._chips.setdefault(norm, chip)

    def chip_for(self, text: str) -> StarterChip | None:
        return self._chips.get(lx.normalize(text))

    def route(self, text: str, *, pending: CardInstance | None = None) -> Route | None:
        """The fast path for ``text``, or None when it needs the understander (C1)."""
        norm = lx.normalize(text)
        if not norm:
            return None
        words = len(norm.split())
        if lx.is_phrase(text, "start_over"):
            return Route("start_over")
        if lx.is_phrase(text, "stop"):
            return Route("stop")
        if lx.is_phrase(text, "pause"):
            return Route("pause")
        if lx.contains_phrase(text, "finish_with_defaults", min_words=2) and words <= SHORT:
            return Route("finish_with_defaults")
        if lx.is_phrase(text, "greeting"):
            return Route("greeting")
        if lx.is_phrase(text, "you_decide") or (
            words <= SHORT - 1 and lx.contains_phrase(text, "you_decide", min_words=2)
        ):
            return Route("you_decide")
        if (
            pending is not None
            and pending.card.type == "visual3"
            and (
                lx.is_phrase(text, "none_of_these")
                or (words <= 5 and lx.contains_phrase(text, "none_of_these"))
            )
        ):
            return Route("none_of_these")
        chip = self.chip_for(text)
        if chip is not None:
            return Route("chip", chip)
        return None
