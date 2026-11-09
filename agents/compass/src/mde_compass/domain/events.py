"""Inbound and outbound events (prompt 5.7). Stable: React will reuse the same shapes later.

Inbound events are idempotent by ``event_id`` (a repeated id is acknowledged and ignored). Every
outbound event is stored with a per-session sequence number for SSE replay (``Last-Event-ID``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from mde_compass.domain.cards import Card
from mde_compass.domain.changes import FeedbackReason, PatchOp
from mde_compass.domain.common import Strict
from mde_compass.domain.interview import BatchGroup, PlannerMode
from mde_compass.domain.research import ResearchRequest

EVENT_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"


class _InboundBase(Strict):
    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    contributor_id: str | None = None  # enterprise: who is speaking


class UserMessage(_InboundBase):
    type: Literal["user_message"] = "user_message"
    text: str = Field(max_length=20_000)
    input_mode: Literal["text", "voice"] = "text"
    lang_hint: str | None = None


class CardAnswer(_InboundBase):
    type: Literal["card_answer"] = "card_answer"
    card_id: str
    value: JsonValue = (
        None  # option id, list of ids (pick_many), ordered ids (priority), text, or form dict
    )
    custom_text: str | None = None


class CardSkip(_InboundBase):
    type: Literal["card_skip"] = "card_skip"
    card_id: str


class CardYouDecide(_InboundBase):
    type: Literal["card_you_decide"] = "card_you_decide"
    card_id: str


class CardBack(_InboundBase):
    type: Literal["card_back"] = "card_back"


class BatchAnswerItem(Strict):
    card_id: str
    action: Literal["answer", "skip", "you_decide"] = "answer"
    value: JsonValue = None
    custom_text: str | None = None


class BatchAnswer(_InboundBase):
    """ "Answer all at once": the answers of one batch form, applied in a single turn."""

    type: Literal["batch_answer"] = "batch_answer"
    batch_id: str
    answers: list[BatchAnswerItem] = Field(max_length=12)


class AnswerEdit(_InboundBase):
    """Edit an earlier answer from the Questions-and-answers list: a new answer version, new facts, a replan."""

    type: Literal["answer_edit"] = "answer_edit"
    card_id: str
    value: JsonValue = None
    custom_text: str | None = None


class UploadAdded(_InboundBase):
    type: Literal["upload_added"] = "upload_added"
    source_id: str


class LinkAdded(_InboundBase):
    type: Literal["link_added"] = "link_added"
    url: str = Field(max_length=2048)


CommandName = Literal[
    "undo",
    "back",
    "pause",
    "resume",
    "start_over",
    "finish_with_defaults",
    "show_what_you_know",
    "ask_more",  # "Ask me more": the planner's deepen mode (build prompt 02)
    "prepare_questions",  # the UI asks for the first batch while the playback card is read
]


class Command(_InboundBase):
    type: Literal["command"] = "command"
    name: CommandName


class BriefEdit(_InboundBase):
    type: Literal["brief_edit"] = "brief_edit"
    item_id: str  # a path in the ChangeSet grammar, e.g. "idea.summary" or "scope.FTR-004.priority"
    value: JsonValue = None


class BriefFeedback(_InboundBase):
    type: Literal["brief_feedback"] = "brief_feedback"
    target: str  # a section key, a row id or a path
    reason: FeedbackReason = "other"
    text: str = Field(default="", max_length=4000)


class AssumptionDecision(_InboundBase):
    type: Literal["assumption_decision"] = "assumption_decision"
    id: str  # ASM-###
    decision: Literal["accept", "change"]
    value: JsonValue = None  # the new value when decision == "change"


class LookupEdit(_InboundBase):
    type: Literal["lookup_edit"] = "lookup_edit"
    action: Literal["add", "remove"]
    request_id: str | None = None  # remove: the RR-### id
    text: str = Field(default="", max_length=500)  # add: "also check how X handles refunds"


class ContributorSwitch(_InboundBase):
    """Enterprise testing: "Speaking as". ``contributor_id`` (inherited) is the contributor to speak as."""

    type: Literal["contributor_switch"] = "contributor_switch"

    @model_validator(mode="after")
    def _needs_contributor(self) -> ContributorSwitch:
        if not self.contributor_id:
            raise ValueError("contributor_switch needs contributor_id")
        return self


class GateDecision(_InboundBase):
    type: Literal["gate_decision"] = "gate_decision"
    decision: Literal["approve", "request_changes"]
    version: int
    brief_hash: str = ""
    section_hashes: dict[str, str] = Field(default_factory=dict)


InboundEvent = Annotated[
    UserMessage
    | CardAnswer
    | CardSkip
    | CardYouDecide
    | CardBack
    | BatchAnswer
    | AnswerEdit
    | UploadAdded
    | LinkAdded
    | Command
    | BriefEdit
    | BriefFeedback
    | AssumptionDecision
    | LookupEdit
    | ContributorSwitch
    | GateDecision,
    Field(discriminator="type"),
]
INBOUND_ADAPTER: TypeAdapter[InboundEvent] = TypeAdapter(InboundEvent)


# --- outbound -------------------------------------------------------------------------------------


class AssistantMessage(Strict):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    language: str = "en"
    chips: list[str] = Field(default_factory=list)  # "I picked up: ..." chips the user can remove


class AssistantDelta(Strict):
    """Reserved: the gateway cannot stream yet, so this is never emitted today."""

    type: Literal["assistant_delta"] = "assistant_delta"
    text: str


class CardEvent(Strict):
    type: Literal["card"] = "card"
    card: Card


class QuestionBatch(Strict):
    """A planned batch: 2 to 3 themed groups. In one-at-a-time mode the engine also emits a normal ``card``
    event for the current question, so older clients keep working."""

    type: Literal["question_batch"] = "question_batch"
    batch_id: str
    mode: PlannerMode
    ui_mode: Literal["one_at_a_time", "answer_all"] = "one_at_a_time"
    groups: list[BatchGroup]
    total: int = 0  # questions in the batch
    answered: int = 0


class PlaybackItem(Strict):
    key: str  # slot id or item id, editable in place
    label: str
    value: JsonValue = None
    basis: str = "suggestion"


class Playback(Strict):
    type: Literal["playback"] = "playback"
    title: str
    items: list[PlaybackItem]
    unclear: list[str] = Field(default_factory=list)


class Notice(Strict):
    type: Literal["notice"] = "notice"
    code: str
    text: str
    chips: list[str] = Field(default_factory=list)
    severity: Literal["info", "warning"] = "info"


class Progress(Strict):
    type: Literal["progress"] = "progress"
    stage: str
    n: int = 0
    of_about: int = 0
    note: str = ""


class BriefPatch(Strict):
    type: Literal["brief_patch"] = "brief_patch"
    change_set_id: str
    from_version: int
    to_version: int
    summary: str
    ops: list[PatchOp]
    voided_sections: list[str] = Field(default_factory=list)
    undoable: bool = True


class BriefReady(Strict):
    type: Literal["brief_ready"] = "brief_ready"
    version: int
    brief_hash: str
    section_hashes: dict[str, str]
    approvable: bool
    blockers: list[str] = Field(default_factory=list)


class BriefApproved(Strict):
    type: Literal["brief_approved"] = "brief_approved"
    version: int
    brief_hash: str
    baseline: str = "B-G1"


class LookupPreview(Strict):
    type: Literal["lookup_preview"] = "lookup_preview"
    requests: list[ResearchRequest]


class ProbeResultEvent(Strict):
    type: Literal["probe_result"] = "probe_result"
    kind: Literal["research", "blueprint", "design"]
    passed: bool
    gaps: list[str] = Field(default_factory=list)
    round: int = 1


class BudgetWarning(Strict):
    type: Literal["budget_warning"] = "budget_warning"
    spent_usd: str
    cap_usd: str
    note: str = ""


class ErrorEvent(Strict):
    type: Literal["error"] = "error"
    code: str
    message: str


OutboundEvent = Annotated[
    AssistantMessage
    | AssistantDelta
    | CardEvent
    | QuestionBatch
    | Playback
    | Notice
    | Progress
    | BriefPatch
    | BriefReady
    | BriefApproved
    | LookupPreview
    | ProbeResultEvent
    | BudgetWarning
    | ErrorEvent,
    Field(discriminator="type"),
]
OUTBOUND_ADAPTER: TypeAdapter[OutboundEvent] = TypeAdapter(OutboundEvent)


class StoredEvent(Strict):
    """A row of the append-only event log (both directions) as the API and the SSE stream see it."""

    idx: int  # per-session order across both directions
    direction: Literal["in", "out"]
    type: str
    event_id: str | None = None  # inbound id (also the "turn id" the outbound events answer)
    turn_id: str | None = None
    seq: int | None = None  # outbound only: the SSE id
    payload: dict[str, JsonValue]
    at: datetime | None = None
