"""Wire models of ``cmp.decide_answers`` (strict-schema-safe: flat, every field required, no open objects).

One call decides up to twelve questions the person left to us ("you decide", "decide the rest"). The model proposes;
code (``adapter``) keeps a decision only when it is valid for its question and otherwise replaces it with the
deterministic choice. Everything that carries the person's words or a model-written question is data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------------ input
class OptionWire(Wire):
    id: str  # the option id the decision must use (A, B, C ...)
    label: str
    basis: str  # your_document | your_words | typical | suggestion, or "" when unknown


class ItemWire(Wire):
    """One question to decide. All of it is data: the question text is model-written from the person's words."""

    card_id: str  # Q-###
    question: str
    card_type: str  # choose_one, pick_many, priority, confirm, open_prompt ...
    needs: list[str]  # the need ids it serves
    answer_spec: str  # what a good answer contains, or ""
    options: list[OptionWire]  # empty for an open question
    recommended_id: str  # the planner's own recommendation (an option id), or ""
    must_confirm: bool  # a risky need: the decision is listed for approval, never silently kept


class DecideInput(Wire):
    language: str  # "en" | "hi" | "hi-Latn": rationale and answer_text are written in it
    project_card: str  # idea one-liner, category, geography, audience, apps, language
    fact_digest: str  # one line per active fact
    hypothesis: str  # the domain hypothesis as one text, or ""
    items: list[ItemWire]  # at most 12


# ----------------------------------------------------------------------------------------------- output
class DecisionWire(Wire):
    card_id: str  # one of the input items
    option_ids: list[
        str
    ]  # option ids of THAT item: exactly one for choose_one and confirm, one or more for
    # pick_many (and priority: in order of importance); empty for an open question
    answer_text: str  # an open question's proposed answer, in the person's language; "" for an option question
    rationale: str  # one short line why (at most 160 characters), in the person's language
    confidence: float  # 0 to 1: how sure the facts make you


class DecideOutput(Wire):
    decisions: list[DecisionWire]  # one per input item
