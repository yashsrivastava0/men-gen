"""HTTP request/response models (prompt 5.7). The event bodies themselves are the inbound/outbound unions
in ``mde_compass.domain.events``; these wrap them for the endpoints under ``/v1/compass``.

Money is a Decimal string (USD) in the API, integer micro-dollars in the store, never float.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mde_compass.domain.common import Strict
from mde_compass.domain.events import StoredEvent
from mde_compass.domain.session import Mode, SessionState


class CreateSessionRequest(Strict):
    mode: Mode = "guided"
    locale: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
    user_label: str = Field(default="", max_length=120)


class SessionSummary(Strict):
    id: str
    title: str = ""
    user_label: str = ""
    mode: Mode = "guided"
    locale: str = "en"
    state: SessionState = "WELCOME"
    brief_version: int = 0
    approved_version: int | None = None
    question_n: int = 0
    question_of_about: int = 0
    spent_usd: str = "0.0000"
    llm_calls: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class SessionList(Strict):
    sessions: list[SessionSummary]


class EventAck(Strict):
    """Reply to ``POST /sessions/{id}/events``: accepted or a duplicate, plus the events it produced
    (also delivered on the SSE stream) so simple clients need no stream."""

    accepted: bool = True
    duplicate: bool = False
    events: list[StoredEvent] = Field(default_factory=list)


class GateResponse(Strict):
    accepted: bool
    state: SessionState
    version: int
    brief_hash: str = ""
    baseline: str | None = None
    blockers: list[str] = Field(default_factory=list)


class VersionInfo(Strict):
    version: int
    hash: str
    baseline: str | None = None
    change_set_id: str | None = None
    created_at: str | None = None


class VersionList(Strict):
    versions: list[VersionInfo]


class DiffEntry(Strict):
    path: str
    before: JsonValue = None
    after: JsonValue = None
    kind: Literal["added", "removed", "changed"]


class DiffResponse(Strict):
    from_version: int
    to_version: int
    sections_changed: list[str]
    entries: list[DiffEntry]


class HistoryResponse(Strict):
    items: list[StoredEvent]
    next_after_idx: int | None = None
    highlighted_turn_ids: list[str] = Field(
        default_factory=list
    )  # turns behind a selected row (?slot=)


class ProvenanceEntry(Strict):
    type: str
    ref: str = ""
    quote: str = ""
    date: str | None = None
    who: str | None = None


class ProvenanceResponse(Strict):
    item_id: str
    value: JsonValue = None
    tier: str = "Open"
    status: str = "empty"
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    value_history: list[dict[str, JsonValue]] = Field(default_factory=list)
    turn_ids: list[str] = Field(default_factory=list)  # to highlight in the history drawer


class HandoffResponse(Strict):
    version: int
    brief_hash: str
    views: dict[
        str, dict[str, JsonValue]
    ]  # research, blueprint, design, verify (never any transcript)


class BudgetResponse(Strict):
    mode: Literal["mock", "live"] = "mock"
    cap_usd: str
    hard_cap_usd: str
    effective_stop_usd: str
    per_session_cap_usd: str
    spent_usd: str
    remaining_usd: str
    calls: int = 0


class CallLogItem(Strict):
    call_id: str
    spec_id: str
    outcome: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: str = "0.0000"
    latency_ms: int = 0


class ErrorBody(Strict):
    code: str
    message: str
