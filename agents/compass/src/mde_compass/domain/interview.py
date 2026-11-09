"""Interview Memory and the Needs Registry vocabulary (build prompt 02; ADR 0015; doc 11 section 17).

Pure data: no I/O, no model calls. Three families of models live here:

* **Memory**: ``Fact`` (the long-term memory, one atomic statement with a verbatim quote), ``QaRecord`` (one
  question and its answer, versioned), ``InterviewSummary`` (rolling per-topic summary) and ``BatchRecord`` (one
  planner run). Facts are never deleted; a correction supersedes, a conflict marks both ``disputed``.
* **Needs**: ``AnswerSpec`` (what a sufficient answer contains) and ``NeedStatus`` (what code computes per need
  each round: ``satisfied | partial | missing | assumed``).
* **Planner vocabulary** shared by the engine, the validators and the events: ``PlannerMode``, ``BatchGroup``,
  ``AuditVerdict``, ``RejectedQuestion``, ``BacklogItem``, ``AssumedInstead``, ``PlannedBatch``.

A planned question is a ``Card`` (see ``domain.cards``) carrying the planner's extra fields, so the answer paths,
the validators, the store and the UI keep one unit.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue, field_validator

from mde_compass.domain.cards import Card, QuestionOrigin  # noqa: F401  (re-exported vocabulary)
from mde_compass.domain.common import Strict
from mde_compass.domain.situation import DomainHypothesis

# --------------------------------------------------------------------------------------------- facts
FactType = Literal[
    "requirement",
    "rule",
    "constraint",
    "preference",
    "number",
    "example",
    "goal",
    "role",
    "exclusion",
    "reference",
    "glossary",
    "context",
]
FACT_TYPES: tuple[str, ...] = (
    "requirement",
    "rule",
    "constraint",
    "preference",
    "number",
    "example",
    "goal",
    "role",
    "exclusion",
    "reference",
    "glossary",
    "context",
)
FactStatus = Literal["active", "superseded", "disputed", "not_used"]
FactOrigin = Literal["stated", "tap", "assumption", "document"]
"""``stated``: the user's own words; ``tap``: a chosen option (the quote is the option label); ``assumption``:
a default or a model proposal (no quote, never Locked); ``document``: a statement read from an uploaded source."""
EffectMode = Literal["set", "add", "remove"]

FACT_ID_RE = re.compile(r"^FCT-\d{3,}$")


class FactEffect(Strict):
    """What a fact does to one slot. ``value_json`` is JSON text, parsed and validated against the slot's
    ``value_schema`` by the projector (strict-schema-safe: no open objects on the wire)."""

    slot_id: str
    value_json: str = "null"
    mode: EffectMode = "set"


class Fact(Strict):
    id: str  # FCT-### (ids are never reused)
    type: FactType
    statement: str = Field(
        min_length=1, max_length=1000
    )  # atomic, normalized to the user's vocabulary
    topics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)  # downstream agents that need it
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: FactStatus = "active"
    origin: FactOrigin = "stated"
    source_ref: str = ""  # evt_... | SRC-###:pointer | the card id of a tap
    qa_card_id: str | None = None  # the question this answered, when there was one
    claim_id: str | None = None  # the document claim it was projected from
    quote: str = ""  # verbatim, verified against the source text by code
    unverified_quote: bool = (
        False  # the quote was not found in its source: never supports a Locked item
    )
    supersedes: list[str] = Field(default_factory=list)
    not_used_reason: str = ""  # required when status == "not_used" (for example "small talk")
    effects: list[FactEffect] = Field(default_factory=list)
    # Which parts of which needs this fact answers, as "need_id:aspect" entries ("need_id:*" = a complete
    # answer, which is what a tapped option is). Code computes need status from these (engine.needs).
    aspects: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not FACT_ID_RE.match(value):
            raise ValueError("fact id must look like FCT-001")
        return value

    def supports_locked(self) -> bool:
        """A fact may lead to a Locked slot only with a verified, user-sourced quote."""
        return (
            self.status == "active"
            and self.origin in ("stated", "tap", "document")
            and bool(self.quote.strip())
            and not self.unverified_quote
        )


# ----------------------------------------------------------------------------------------------- Q&A
QaStatus = Literal["open", "answered", "skipped", "you_decide", "assumed", "superseded", "dropped"]
InputMode = Literal["tap", "text", "voice"]
PlannerMode = Literal["initial", "followup", "replan", "deepen", "simplify"]
PLANNER_MODES: tuple[str, ...] = ("initial", "followup", "replan", "deepen", "simplify")


class QaRecord(Strict):
    """One question and its answer. An edit creates a new ``version`` and supersedes the old one."""

    card_id: str  # Q-###
    version: int = Field(default=1, ge=1)
    batch_id: str | None = None
    group_id: str | None = None
    mode: PlannerMode | None = None
    card: Card  # the full question object as it was shown (text, options with basis labels, serves, unlocks)
    status: QaStatus = "open"
    answer: JsonValue = None  # option id(s), form dict or text, as the user sent it
    custom_text: str | None = None
    input_mode: InputMode = "tap"
    parent_card_id: str | None = None  # the question a follow-up refers to
    asked_at: datetime | None = None
    answered_at: datetime | None = None


# ---------------------------------------------------------------------------------------------- summary
class SummaryTopic(Strict):
    topic: str
    decisions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(default_factory=list)  # the user's own words for things
    tone: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)


class InterviewSummary(Strict):
    """A rolling, structured summary by topic, refreshed after each batch; used only when the facts exceed the
    working-context budget (it never replaces the ledger)."""

    version: int = Field(default=0, ge=0)
    topics: list[SummaryTopic] = Field(default_factory=list)
    through_fact_id: str = ""  # the newest fact the summary covers
    updated_at: datetime | None = None


# ------------------------------------------------------------------------------------------------ needs
class AnswerSpec(Strict):
    """What a *sufficient* answer to a need contains for its consumers (data in ``catalog/slots.yaml``)."""

    sufficient: str = Field(min_length=1)
    aspects: list[str] = Field(
        default_factory=list
    )  # named parts an answer can cover (who, goal, where, ...)
    fact_types: list[FactType] = Field(default_factory=list)  # fact types that count as an answer
    min_facts: int = Field(default=1, ge=0)


NeedState = Literal["satisfied", "partial", "missing", "assumed"]


class NeedStatus(Strict):
    """Computed by code each round (``engine.needs``); the model only reports which aspects an answer covered."""

    need_id: str
    state: NeedState
    impact: int = Field(ge=1, le=5)
    level: str = ""  # must | should | may | generated
    covered_aspects: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    assumed_value: JsonValue = None
    asked: int = 0  # questions asked for it so far
    followups_used: int = 0


# ------------------------------------------------------------------------------ planner vocabulary
class BatchGroup(Strict):
    """A themed group of 3 to 4 questions with a model-written title in the user's language."""

    id: str
    title: str
    cards: list[Card] = Field(default_factory=list)


class AuditVerdict(Strict):
    """The auditor's review of one question (``cmp.audit_questions``). It never rewrites the question."""

    card_id: str
    specific: int = Field(ge=1, le=5)
    leading: bool = False
    jargon: bool = False
    duplicate_of: str | None = None
    answerable: bool = True
    maps_to_need: bool = True
    issues: list[str] = Field(default_factory=list)
    fix: str = ""


class RejectedQuestion(Strict):
    card_id: str
    source: Literal["code", "auditor"]
    reasons: list[str] = Field(default_factory=list)


class BacklogItem(Strict):
    topic: str
    intent: str = ""
    serves: list[str] = Field(default_factory=list)


class AssumedInstead(Strict):
    """A need the planner chose not to ask, with the assumption it proposes (shown to the user as a chip)."""

    need_id: str
    assumption: str
    reason: str = ""


class PlannedBatch(Strict):
    """The validated result of one planner run, ready for the engine."""

    batch_id: str
    mode: PlannerMode
    groups: list[BatchGroup] = Field(default_factory=list)
    covered_needs: list[str] = Field(default_factory=list)
    assumed_instead: list[AssumedInstead] = Field(default_factory=list)
    expert_gaps: list[str] = Field(default_factory=list)
    backlog: list[BacklogItem] = Field(default_factory=list)
    domain_hypothesis: DomainHypothesis | None = None  # mode "initial" only

    def cards(self) -> list[Card]:
        return [card for group in self.groups for card in group.cards]


class InterviewBlock(Strict):
    """Counts and coverage of the interview, stored in ``Brief.meta.interview`` for audit. It is bookkeeping about
    the interview, not content: it never enters the Brief hash and never reaches a consumer view (ADR 0015)."""

    budget: Literal["quick", "standard", "thorough"] = "standard"
    questions_asked: int = 0
    followups: int = 0
    answered: int = 0
    skipped: int = 0
    assumed: int = 0
    batches: int = 0
    facts_active: int = 0
    facts_not_used: int = 0
    facts_unreferenced: int = (
        0  # active facts no Brief item uses: a defect, placed in domain_facts and counted
    )
    coverage: float = Field(
        default=1.0, ge=0.0, le=1.0
    )  # share of active facts referenced or marked not_used
    fallback_count: int = 0  # a fallback is a failure: counted (live batches must show 0)
    question_ids: list[str] = Field(default_factory=list)
    stop_reason: str = ""


class BatchRecord(Strict):
    """One planner run as stored (``compass_batches``). ``inputs_hash`` makes a replan idempotent."""

    id: str  # BAT-###
    mode: PlannerMode
    inputs_hash: str
    output: dict[str, JsonValue] = Field(default_factory=dict)  # the validated planner output
    accepted: list[str] = Field(default_factory=list)  # card ids shown
    rejected: list[RejectedQuestion] = Field(default_factory=list)
    verdicts: list[AuditVerdict] = Field(default_factory=list)
    used_fallback: bool = False  # a fallback is a failure: flagged and counted
    repaired: bool = False
    latency_ms: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    created_at: datetime | None = None
