"""spec.event.v1: the resumable event stream of a run (docs/api/spec-agent.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "run.queued",
    "run.started",
    "plan.ready",
    "step.started",
    "step.finished",
    "call.finished",
    "validators.done",
    "repair.rcode",
    "repair.reask",
    "banner",
    "gate.opened",
    "gate.closed",
    "checkpoint",
    "run.partial",
    "run.resumed",
    "run.finished",
    "run.failed",
]
TERMINAL: frozenset[str] = frozenset({"run.finished", "run.failed", "run.partial"})
PartialReason = Literal[
    "budget_cap", "budget_tripwire", "stop", "rate_limit", "no_credit", "crash", "plan_invalid"
]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=1)
    type: EventType
    at: str
    run_id: str
    step: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
