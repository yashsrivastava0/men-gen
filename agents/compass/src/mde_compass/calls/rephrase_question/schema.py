"""Wire models of ``cmp.rephrase_question`` (strict-schema-safe: flat, every field required, no open objects).

The call changes the *wording* of one planned question and nothing else: same intent, same answer spec, and
exactly the same options in the same order (so the person's earlier taps and the engine's option ids keep their
meaning). Code maps the new labels back onto the original option objects by index.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Mode = Literal["rephrase", "simplify"]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionWire(Wire):
    label: str
    description: str  # a short line under the label, or ""


class RephraseInput(Wire):
    language: str  # "en" | "hi" | "hi-Latn"
    mode: Mode
    intent: str  # what the question is trying to learn ("" for an older card)
    answer_spec: str  # what a good answer contains ("" for an older card)
    question: str  # the question the person did not understand
    why_we_ask: str
    type: str  # card type: choose_one, pick_many, open_prompt ...
    options: list[OptionWire]  # in order; empty for free-text questions
    project_terms: list[str]  # the project's own words (may be empty)
    recent_user_words: str  # what the person said recently ("" when nothing); untrusted


class RephraseOutput(Wire):
    question: str
    why_we_ask: str
    options: list[OptionWire]  # EXACTLY as many as the input, in the same order
    examples: list[str]  # placeholder hints for a free-text question, [] for a choice question
