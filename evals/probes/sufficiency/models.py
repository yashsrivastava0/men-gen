"""Input and output models of the context sufficiency probe (eval tool, not a product call)."""

from __future__ import annotations

from typing import Literal

from mde_contracts.common import Frozen

QuestionKind = Literal["route", "recipe", "visibility", "action_target", "bound_data", "flow"]


class ProbeQuestion(Frozen):
    id: str
    kind: QuestionKind  # what is asked (the offline reader answers by kind)
    subject: str  # the screen id the question is about
    detail: str | None = None  # an action name, for ``action_target``
    text: str  # the question in plain words (what a model reads)


class ProbeInput(Frozen):
    slice_text: str  # the spec slice, and nothing else
    questions: list[ProbeQuestion]


class ProbeAnswer(Frozen):
    id: str
    answer: str  # a short exact answer, or "unknown"
    found: bool  # false when the slice does not say


class ProbeOutput(Frozen):
    answers: list[ProbeAnswer]
