"""Session record, state names and contributors (prompt 6.12, 6.14).

The transition table itself is data in ``mde_compass.engine.state``; these are the shared names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from mde_compass.domain.common import Strict

SessionState = Literal[
    "WELCOME",
    "INTAKE",
    "UNDERSTANDING",
    "INTERVIEWING",
    "PLAYBACK",
    "SEEDING",
    "PROBING",
    "BRIEF_REVIEW",
    "APPROVED",
    "CHANGING",
    "PAUSED",
    "ARCHIVED",
]
SESSION_STATES: tuple[str, ...] = (
    "WELCOME",
    "INTAKE",
    "UNDERSTANDING",
    "INTERVIEWING",
    "PLAYBACK",
    "SEEDING",
    "PROBING",
    "BRIEF_REVIEW",
    "APPROVED",
    "CHANGING",
    "PAUSED",
    "ARCHIVED",
)
Mode = Literal["guided", "enterprise"]
ContributorRole = Literal["requester", "approver", "commenter"]


class Contributor(Strict):
    id: str
    name: str = ""
    role: ContributorRole = "requester"
    decision_owner: bool = False


class SessionRecord(Strict):
    id: str  # ses_<ulid>
    tenant_id: str
    user_id: str
    mode: Mode = "guided"
    locale: str = "en"
    user_label: str = ""
    title: str = ""
    state: SessionState = "WELCOME"
    resume_state: SessionState | None = None  # where PAUSED / CHANGING returns to
    brief_version: int = 0
    approved_version: int | None = None
    approved_hash: str | None = None
    spent_micros: int = 0
    llm_calls: int = 0
    outbound_seq: int = 0
    active_turn: bool = False
    row_version: int = 0  # optimistic lock for save()
    contributors: list[Contributor] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
