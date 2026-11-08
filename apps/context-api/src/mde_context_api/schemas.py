"""Request and response models of the Context API (typed, so OpenAPI documents every route).

The event models themselves (``InboundEvent``, ``OutboundEvent`` and everything they carry) come
from ``mde_contracts.events``; the session view comes from ``mde_flow.models``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from mde_contracts.common import Frozen, Mode
from mde_contracts.events import OutboundEvent
from mde_contracts.gates import PlanPhase, SessionState
from mde_flow.models import JobSummary, SessionSnapshot, VersionInfo

PolicyIdStr = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_@.-]*$")]


class CreateSessionRequest(Frozen):
    """Start a new app conversation."""

    mode: Mode = Field(
        default="guided",
        description=(
            "`guided` (the default) or `enterprise`. A tenant whose organisation restricts what "
            "it may start (`TENANT_POLICIES`, or the caller's token entry) gets its own default "
            "when this is omitted, and a mode outside what it may start is refused with `403`."
        ),
    )
    policy_id: PolicyIdStr | None = Field(
        default=None,
        description=(
            "Override the mode's default policy (`guided-default`; Enterprise: the configured "
            "default). It must exist and be a policy of the same mode, and (when the tenant is "
            "bound to a set of policies) one of those, otherwise `403`."
        ),
    )
    org_profile: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Enterprise only: the organisation's standing answers (brand, identity, compliance, "
            "approvers). Stored for the tenant; JSON object, size limited."
        ),
    )


class BudgetSummary(Frozen):
    """The LLM budget as the context API shows it: totals only (micro-dollars).

    No per-scope breakdown (the gateway's own ledger lists every tenant's `session:<id>` spend, so
    it never leaves the service-to-service API) and no tripwire state.
    """

    cap_micros: int
    effective_cap_micros: int
    spent_micros: int
    reserved_micros: int
    live_calls: int = 0


class CreateSessionResponse(Frozen):
    session: SessionSnapshot
    events: list[OutboundEvent] = Field(description="The greeting events (a canned message).")
    org_profile_saved: bool = False


class EventResponse(Frozen):
    """What one inbound event produced, plus where the session and its run stand now."""

    session_id: str
    events: list[OutboundEvent] = Field(
        description="The immediate outbound events. A repeated event id returns the same list."
    )
    state: SessionState
    plan_phase: PlanPhase | None = None
    job: JobSummary | None = Field(
        default=None, description="The plan job, when one is queued or running."
    )
    last_seq: int = Field(description="Highest outbound seq now stored (the SSE cursor).")


class VersionsResponse(Frozen):
    versions: list[VersionInfo]


class HealthResponse(Frozen):
    status: Literal["ok"] = "ok"
    version: str


class ReadyResponse(Frozen):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]
