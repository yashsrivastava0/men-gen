"""Wire models of ``cmp.audit_questions`` (strict-schema-safe: flat, every field required, no open objects).

The auditor reads a batch of planned questions and returns one verdict per question: how specific it is to this
project (1 to 5), whether it is leading, full of jargon, a duplicate, answerable and mapped to a need, the
issues it found and a SHORT hint on how to repair it. It never rewrites a question. Code decides what a verdict
means (reject below 3 or on any flag; one repair of the rejected items; otherwise drop them).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------------ input
class NeedWire(Wire):
    """One need a question may serve (a Needs Registry entry) and what a sufficient answer contains."""

    need_id: str
    label: str
    sufficient: str  # the answer_spec text, or "" when unknown


class QuestionWire(Wire):
    """One question under review. All of it is data written by a model from the person's words."""

    card_id: str  # Q-###
    text: str
    intent: str  # what we want to learn
    serves: list[str]  # need ids, or "domain:<topic>"
    type: str  # card type: choose_one, pick_many, open_prompt ...
    option_labels: list[str]  # labels in order, empty for free-text questions
    recommended: str  # the recommended option's label, or ""
    why_we_ask: str


class AuditInput(Wire):
    language: str  # "en" | "hi" | "hi-Latn"
    project_terms: list[str]  # the project's own vocabulary (idea, facts, glossary)
    needs: list[NeedWire]  # the listed needs; "domain:<topic>" ids are always allowed
    asked_list: str  # earlier questions, one per line: id, intent, status ("" when none)
    questions: list[QuestionWire]


# ----------------------------------------------------------------------------------------------- output
class VerdictWire(Wire):
    card_id: str
    # 1 to 5: 5 only this project could be asked this, 3 acceptable, 1 to 2 generic
    specific: int
    leading: bool
    jargon: bool
    duplicate_of: str  # id of the earlier question (batch or asked list) it repeats, or ""
    answerable: bool  # could this person realistically answer it
    maps_to_need: bool  # serves a listed need or a "domain:<topic>"
    # plain short English; at least one when the score is below 3 or any flag is set
    issues: list[str]
    fix: str  # a SHORT hint on how to repair it, never a rewritten question; "" when nothing to fix


class AuditOutput(Wire):
    verdicts: list[VerdictWire]  # one per input question, same card_id
