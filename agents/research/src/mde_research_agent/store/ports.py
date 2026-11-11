"""The persistence interface of Research (one Protocol; file-backed and PostgreSQL implementations, plus an in-memory test double).

Rules every implementation follows (and the shared contract checks in ``store/contract.py`` verify):

* every call is bound to a TENANT: a run of another tenant behaves as not found;
* one ACTIVE run per (tenant, program): ``create_run`` raises :class:`RunActive` while another run of that program is
  queued, running or committing;
* a committed result (``save_result``) is IMMUTABLE: the hashed ``research.json`` never changes; owner rejections are a
  separate overlay (``add_decision``);
* events are append-only with a contiguous per-run ``seq``; ``events_after`` replays them in order and loses none;
* nothing here spans a model or search call: each method is one short transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mde_core.errors import Conflict
from mde_research_agent.domain.events import Event

ACTIVE_STATUSES = ("queued", "running", "committing")


class RunActive(Conflict):
    """Another run of the program is still in progress."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already in progress for this program ({run_id})")
        self.run_id = run_id


class RunRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    program_id: str
    status: str = "queued"
    brief_hash: str
    brief_version: int = 0
    load_class: str | None = None
    load_score: int | None = None
    plan_source: str | None = None
    export_sha256: str = ""
    input_json: dict[str, Any] | None = None  # the slim ResearchInput (never the raw 4 MB export)
    result_status: str | None = None
    content_hash: str | None = None
    reason: str | None = None
    skip_requested: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)  # what the app records: input, size class, spend
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    item_id: str
    kind: str  # reject
    reason: str = ""
    at: datetime


class Store(Protocol):
    async def create_run(self, row: RunRow) -> None: ...
    async def update_run(self, tenant_id: str, run_id: str, **fields: Any) -> None: ...
    async def get_run(self, tenant_id: str, run_id: str) -> RunRow | None: ...
    async def active_run(self, tenant_id: str, program_id: str) -> RunRow | None: ...
    async def latest_committed(self, tenant_id: str, program_id: str) -> RunRow | None: ...
    async def list_runs(self, tenant_id: str, limit: int = 200) -> list[RunRow]: ...
    async def active_runs(self) -> list[RunRow]: ...
    async def mark_interrupted(self) -> int: ...

    async def append_event(self, event: Event) -> None: ...
    async def events_after(self, run_id: str, seq: int, limit: int = 500) -> list[Event]: ...
    async def last_seq(self, run_id: str) -> int: ...
    async def wait_for_events(self, run_id: str, after_seq: int, timeout: float) -> None: ...  # noqa: ASYNC109

    async def save_result(
        self, run_id: str, research: dict[str, Any], evidence: dict[str, Any], content_hash: str
    ) -> None: ...
    async def get_result(self, run_id: str) -> dict[str, Any] | None: ...
    async def get_evidence(self, run_id: str) -> dict[str, Any] | None: ...

    async def add_decision(self, decision: Decision) -> None: ...
    async def list_decisions(self, run_id: str) -> list[Decision]: ...

    async def aclose(self) -> None: ...
