"""Wire models of ``cmp.plan_questions`` (strict-schema-safe: every field required, no open objects).

The planner returns a *batch*: the domain hypothesis (mode ``initial`` only), questions in 2 to 3 themed groups,
the needs it covers, the needs it chose to assume instead of asking, the expert blind spots it sees and a backlog.
Free-form values travel as plain strings. Code (``engine.question_validators`` and the auditor) decides what is
shown; nothing here controls flow.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Mode = Literal["initial", "followup", "replan", "deepen", "simplify"]
QuestionType = Literal[
    "choose_one", "pick_many", "priority", "confirm", "text", "quick_form", "upload_or_link"
]
Basis = Literal["your_document", "your_words", "typical", "suggestion"]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------------ input
class NeedWire(Wire):
    """One need the planner may serve, with what a sufficient answer is and what is still missing."""

    need_id: str
    label: str
    impact: int
    level: str  # must | should | may | generated
    state: Literal["satisfied", "partial", "missing", "assumed"]
    sufficient: str  # the answer_spec text
    aspects: list[str]
    missing_aspects: list[str]
    card_type: str  # the kind of card its last-resort fallback uses (a hint for the question type)
    option_labels: list[str]  # that fallback card's option labels (grounding for choice questions)
    assumed_value: str  # text of the current assumption, or ""


class PlanInput(Wire):
    mode: Mode
    language: str  # "en" | "hi" | "hi-Latn"
    project_card: (
        str  # idea one-liner, category, geography, audience, apps, mode, language, profile
    )
    fact_digest: str  # one line per active fact (FCT-014 [rule|pricing] statement (from Q-03))
    asked_list: str  # earlier questions: id, intent, status
    last_qa: str  # the last four questions and answers, verbatim
    project_terms: list[
        str
    ]  # words from the idea, facts and glossary: the project's own vocabulary
    needs: list[NeedWire]  # open needs first; satisfied ones are listed only as context
    want_min: int
    want_max: int
    group_min: int
    group_max: int
    remaining_budget: int
    focus_needs: list[str]  # replan / deepen / handoff gaps: work on these first (may be empty)
    focus_card_id: str  # followup and simplify: the exact pair ("" otherwise)
    focus_question: str
    focus_answer: str
    missing_aspects: list[str]  # followup: what the answer still lacks
    pivot_from: str  # replan after a pivot: the old category ("" otherwise)
    style_notes: list[str]  # frame style guides and example phrasings for the needs involved
    next_card_n: int  # number the questions Q-<n>, Q-<n+1>, ... from here


# ----------------------------------------------------------------------------------------------- output
class OptionWire(Wire):
    label: str
    description: str  # a short line under the label, or ""
    basis: Basis
    reason: str  # why this option is offered or recommended, or ""


class QuestionWire(Wire):
    id: str
    group_id: str  # G1, G2, ...: questions with the same group_id form one themed group
    group_title: (
        str  # the group's title in the user's language; repeat it on every question of the group
    )
    intent: str  # what we want to learn, in plain words
    serves: list[str]  # needs it fills, or "domain:<topic>" for project-specific details
    unlocks: list[
        str
    ]  # which downstream decision it feeds (Research query, Blueprint roles, Design tone...)
    answer_spec: str  # what a good answer contains
    depends_on: list[str]  # ids of questions in this batch that should come first
    priority: int  # 1 (most important) to 5
    type: QuestionType
    text: str
    why_we_ask: str
    options: list[OptionWire]  # empty for text, quick_form and upload_or_link
    recommended: str  # the label of the recommended option, or ""
    skip_assumes: str  # what we assume if the user skips, or ""
    followup_hint: str  # what to ask if the answer is vague, or ""
    examples: list[str]  # placeholder hints for text questions


class FeatureWire(Wire):
    name: str
    intent: str
    custom: bool  # outside the seed capability families


class GlossaryWire(Wire):
    term: str
    synonyms: list[str]


class HypothesisWire(Wire):
    """The domain hypothesis (mode ``initial``); every field is a guess and is labelled one downstream."""

    category: str
    subcategory: str
    business_model_candidates: list[str]
    likely_roles: list[str]
    likely_features: list[FeatureWire]
    hard_parts: list[str]
    glossary_candidates: list[GlossaryWire]
    confidence: float


class AssumedInsteadWire(Wire):
    need_id: str
    assumption: str
    reason: str


class BacklogWire(Wire):
    topic: str
    intent: str
    serves: list[str]


class PlanOutput(Wire):
    domain_hypothesis: HypothesisWire  # empty values outside mode "initial"
    # Flat on purpose: the gateway's strict schemas allow five levels of nesting. Code regroups by group_id.
    questions: list[QuestionWire]
    covered_needs: list[str]
    assumed_instead: list[AssumedInsteadWire]
    expert_gaps: list[str]
    backlog: list[BacklogWire]
