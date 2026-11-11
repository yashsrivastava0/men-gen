"""``research.event.v1``: what the run tells the UI, and the run snapshot a reloaded page starts from.

Events are stored (replay after a reload) and streamed over SSE with ``Last-Event-ID`` resume. The user-facing
``label`` is built by CODE from templates and the Brief's own ``shown_to_user_as`` strings, never written by a
model at run time. Events carry domains, titles and counts, NEVER page text or quotes (RV-17): each type has an
allowlist of ``detail`` fields and a length cap, enforced where events are created.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EVENT_SCHEMA = "research.event.v1"
EventType = Literal[
    "run.started",
    "plan.ready",
    "target.started",
    "target.covered",
    "target.not_found",
    "target.cancelled",
    "query.issued",
    "search.results",
    "page.reading",
    "page.read",
    "page.skipped",
    "fact.found",
    "fact.verified",
    "fact.rejected",
    "entity.discovered",
    "round.completed",
    "gap.detected",
    "warning",
    "run.finished",
    "run.skipped",
    "run.failed",
]
TERMINAL_EVENTS = frozenset({"run.finished", "run.skipped", "run.failed"})
RunStatus = Literal[
    "queued",
    "running",
    "committing",
    "complete",
    "partial",
    "failed",
    "skipped",
    "interrupted",
]
TargetStatus = Literal[
    "queued", "running", "covered", "not_found", "cancelled", "skipped", "partial"
]

# The only ``detail`` fields each event type may carry, and the longest string allowed in them.
DETAIL_ALLOWLIST: dict[str, frozenset[str]] = {
    "run.started": frozenset(
        {"load_class", "load_score", "minutes", "plan_source", "degraded", "depth_word"}
    ),
    "plan.ready": frozenset({"targets", "declined", "planned_searches", "plan_source", "country"}),
    "target.started": frozenset({"job", "lane", "round", "topic"}),
    "target.covered": frozenset({"job", "slots_filled", "slots_required", "topic"}),
    "target.not_found": frozenset({"job", "slots_filled", "slots_required", "queries", "topic"}),
    "target.cancelled": frozenset({"job", "topic"}),
    "query.issued": frozenset({"query", "variant", "lang", "country", "provider", "domain"}),
    "search.results": frozenset({"count", "domains", "query"}),
    "page.reading": frozenset({"domain", "title"}),
    "page.read": frozenset({"domain", "title", "chars"}),
    "page.skipped": frozenset({"domain", "title", "reason"}),
    "fact.found": frozenset({"kind", "label", "topic"}),
    "fact.verified": frozenset({"kind", "label", "domain", "level", "topic"}),
    "fact.rejected": frozenset({"reason", "topic", "label"}),
    "entity.discovered": frozenset({"name", "kind"}),
    "round.completed": frozenset({"round", "slots_filled", "slots_required", "new_slots"}),
    "gap.detected": frozenset({"targets", "count"}),
    "warning": frozenset({"code", "message"}),
    "run.finished": frozenset({"status", "facts", "pages", "searches", "usd", "elapsed_s"}),
    "run.skipped": frozenset({"status", "facts", "pages", "searches", "usd", "elapsed_s"}),
    "run.failed": frozenset({"status", "reason", "code"}),
}
MAX_DETAIL_STRING = 200


class Counters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    searches_used: int = 0
    searches_planned: int = 0
    searches_ceiling: int = 0
    pages_read: int = 0
    facts_verified: int = 0
    targets_done: int = 0
    targets_total: int = 0
    elapsed_s: int = 0
    eta_s: int | None = None


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    seq: int
    ts: datetime
    run_id: str
    type: EventType
    target_id: str | None = None
    round: int | None = None
    phase: str | None = None
    label: str
    detail: dict[str, Any] = Field(default_factory=dict)
    counters: Counters = Field(default_factory=Counters)
    schema_: str = Field(default=EVENT_SCHEMA, alias="schema")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TargetView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    job: str
    lane: str
    priority: int = 0
    status: TargetStatus = "queued"
    slots_filled: int = 0
    slots_required: int = 0


class RunSnapshot(BaseModel):
    """``GET /runs/{id}``: everything a reloaded page needs to draw the run, then tail from ``last_seq``."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    program_id: str
    status: RunStatus
    phase: str | None = None
    round: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    load_class: str | None = None
    load_score: int | None = None
    depth_word: str | None = None
    plan_source: str | None = None
    input_quality: dict[str, Any] = Field(default_factory=dict)
    counters: Counters = Field(default_factory=Counters)
    targets: list[TargetView] = Field(default_factory=list)
    declined: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    feed: list[Event] = Field(default_factory=list)
    last_seq: int = 0
    result_status: str | None = None
    content_hash: str | None = None
    reason: str | None = None


def clamp_detail(event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Keep only the allowed fields of ``detail`` and cut long strings (RV-17)."""
    allowed = DETAIL_ALLOWLIST.get(event_type, frozenset())
    return {k: _clamp(v) for k, v in detail.items() if k in allowed}


def _clamp(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING]
    if isinstance(value, (list, tuple)):
        return [_clamp(v) for v in value][:50]
    if isinstance(value, dict):
        return {str(k): _clamp(v) for k, v in value.items()}
    return value
