"""The HTTP routes. No business logic here: every route authenticates, validates, calls one
``FlowService`` method with the caller's ``Principal`` (which binds every unit of work to the
caller's tenant) and shapes the answer.

See ``docs/specs/events-and-api.md`` for the full contract.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from typing import Annotated, Any, Final, cast

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import JsonValue

from mde_context_api import __version__
from mde_context_api.auth import CallerDep, PrincipalDep
from mde_context_api.composition import AppState
from mde_context_api.errors import ErrorBody
from mde_context_api.inbound import validate_inbound
from mde_context_api.limits import StreamLease
from mde_context_api.schemas import (
    BudgetSummary,
    CreateSessionRequest,
    CreateSessionResponse,
    EventResponse,
    HealthResponse,
    ReadyResponse,
    VersionsResponse,
)
from mde_context_api.sse import MAX_CURSOR, event_stream, parse_last_event_id
from mde_contracts.brief import Brief
from mde_contracts.events import InboundEvent, StateChanged
from mde_contracts.plan import PlanView
from mde_core.errors import NotFound, ValidationFailed
from mde_core.logging import get_logger
from mde_flow.models import SessionSnapshot

SessionId = Annotated[str, Path(min_length=1, max_length=128, description="Session id (ses_...)")]
Version = Annotated[int | None, Query(ge=1, description="A specific version; default: latest")]

log = get_logger("mde_context_api")
LEDGER_CHECK_SECONDS: Final = 3.0


async def state_dependency(request: Request) -> AppState:
    return await request.app.state.holder.get()


StateDep = Annotated[AppState, Depends(state_dependency)]

_PROBLEMS: Final[dict[int, str]] = {
    401: "Missing or invalid credentials",
    403: "Not the owner or a required approver",
    404: "Not found (also another tenant's id)",
    409: "Conflict, stale hash, or not allowed here",
    413: "Request body too large",
    415: "Unsupported upload type (for example a PDF)",
    422: "The request is not valid",
    429: "Too many requests or open streams, or the budget is used up",
}


def problems(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` for the error statuses a route can return (all ``ErrorBody``)."""
    return {c: {"model": ErrorBody, "description": _PROBLEMS[c]} for c in codes}


sessions = APIRouter(prefix="/v1/sessions", tags=["sessions"])
ops = APIRouter(tags=["ops"])
dev = APIRouter(prefix="/v1", tags=["dev"])


# --------------------------------------------------------------------------- sessions


@sessions.post(
    "",
    status_code=201,
    summary="Create a session",
    responses=problems(401, 403, 413, 422, 429),
    description=(
        "Starts an app conversation in DISCOVERING and returns the session plus the greeting "
        "events (a canned message with starter chips; no LLM call). Rate limited per caller and "
        "per tenant (`429` with `Retry-After`). A tenant bound to a set of policies "
        "(`TENANT_POLICIES`, or the caller's token entry) can only start those: anything else is "
        "`403`. An Enterprise session starts from the tenant's Org Profile: the one posted in "
        "`org_profile` (stored first, replacing the old one) or else the stored one; Discover turns "
        "it into assumed slots. A Guided session never reads it."
    ),
)
async def create_session(
    body: CreateSessionRequest, caller: CallerDep, state: StateDep
) -> CreateSessionResponse:
    principal = caller.principal
    state.limits.sessions.hit(principal)  # every live session may spend up to the session cap
    requested_mode = body.mode if "mode" in body.model_fields_set else None
    mode, policy_id = state.policies.resolve_new(  # 403 outside the binding; unknown/mismatch: 422
        requested_mode, body.policy_id, state.binding_for(caller)
    )
    if body.org_profile is not None and mode != "enterprise":
        raise ValidationFailed(
            "An organisation profile is only used in Enterprise sessions.",
            details={"field": "org_profile"},
        )
    profile: dict[str, JsonValue] | None = None
    saved = False
    if mode == "enterprise":
        if body.org_profile is not None:  # stored FIRST, so Discover starts from it
            _check_profile_size(body.org_profile, state)
            await state.org_profiles.put(principal.tenant_id, body.org_profile)
            profile, saved = body.org_profile, True
        else:  # else the tenant's stored profile (another tenant's is never visible here)
            profile = await state.org_profiles.get(principal.tenant_id)
    rec, events = await state.flow.create_session(principal, mode, policy_id, org_profile=profile)
    snapshot = await state.flow.get_snapshot(principal, rec.id)
    return CreateSessionResponse(session=snapshot, events=events, org_profile_saved=saved)


def _check_profile_size(profile: Mapping[str, JsonValue], state: AppState) -> None:
    size = len(json.dumps(profile, separators=(",", ":")).encode("utf-8"))
    if size > state.settings.max_org_profile_bytes:
        raise ValidationFailed(
            f"The organisation profile is too large (limit {state.settings.max_org_profile_bytes} bytes).",
            details={"field": "org_profile"},
        )


@sessions.get(
    "/{session_id}", summary="Where the session stands", responses=problems(401, 403, 404, 422)
)
async def get_session(
    session_id: SessionId, principal: PrincipalDep, state: StateDep
) -> SessionSnapshot:
    """State, plan phase, the pending card or open gate, a slot summary and the plan job."""
    return await state.flow.get_snapshot(principal, session_id)


@sessions.post(
    "/{session_id}/events",
    summary="Send an inbound event",
    responses={
        200: {"description": "Processed; the replies are in `events`"},
        202: {
            "model": EventResponse,
            "description": "Processed and a plan job was enqueued (approving Gate 1)",
        },
        **problems(401, 403, 404, 409, 413, 415, 422, 429),
    },
)
async def post_event(
    session_id: SessionId,
    event: InboundEvent,
    principal: PrincipalDep,
    state: StateDep,
    response: Response,
) -> EventResponse:
    """One of `user_message`, `option_selected`, `upload_added`, `gate_decision`, `ui_action`.

    Idempotent by `event_id`: a repeat returns the original replies and has no second effect.
    Discover turns answer synchronously. Approving Gate 1 enqueues the plan job (research, build);
    the response is then `202` and the job's progress arrives on the stream.
    """
    validate_inbound(event, state.settings)
    replies = await state.flow.handle_event(principal, session_id, event)
    snap = await state.flow.get_snapshot(principal, session_id)
    if any(isinstance(e, StateChanged) and e.to_state == "PLANNING" for e in replies):
        response.status_code = 202
    return EventResponse(
        session_id=session_id,
        events=replies,
        state=snap.state,
        plan_phase=snap.plan_phase,
        job=snap.active_job,
        last_seq=snap.last_seq,
    )


@sessions.get(
    "/{session_id}/stream",
    summary="Outbound events as server-sent events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "`text/event-stream`. Each frame has `id` (the event's seq), `event` (its type) "
                "and `data` (the event as JSON). Reconnect with `Last-Event-ID` (or `after`) to "
                "replay what was missed."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **problems(401, 403, 404, 422, 429),
    },
)
async def stream(
    request: Request,
    session_id: SessionId,
    principal: PrincipalDep,
    state: StateDep,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID", description="Resume after this seq")
    ] = None,
    after: Annotated[
        int | None, Query(ge=0, le=MAX_CURSOR, description="Resume after this seq")
    ] = None,
    follow: Annotated[bool, Query(description="false: send what is stored, then close")] = True,
    limit: Annotated[
        int | None, Query(ge=1, le=100_000, description="Close after this many events")
    ] = None,
    timeout_s: Annotated[
        float | None, Query(alias="timeout", gt=0, description="Close after this many seconds")
    ] = None,
) -> StreamingResponse:
    settings = state.settings
    cursor = parse_last_event_id(last_event_id)
    if cursor is None:
        cursor = after or 0
    max_seconds = (
        min(timeout_s, settings.sse_max_seconds) if timeout_s else settings.sse_max_seconds
    )
    # a live stream holds a slot for as long as it runs (a bounded read does not: it ends at once)
    lease = state.limits.streams.acquire(principal, ttl_seconds=max_seconds) if follow else None
    try:
        # read the first batch now: a missing or foreign session is a 404, not a broken stream
        first = await state.flow.list_events(
            principal, session_id, after_seq=cursor, limit=settings.sse_batch_size
        )
    except BaseException:
        if lease is not None:
            lease.release()
        raise
    body = event_stream(
        state.flow,
        principal,
        session_id,
        after_seq=cursor,
        poll_seconds=settings.sse_poll_seconds,
        max_poll_seconds=settings.sse_max_poll_seconds,
        keepalive_seconds=settings.sse_keepalive_seconds,
        max_seconds=max_seconds,
        batch=settings.sse_batch_size,
        follow=follow,
        limit=limit,
        first=first,
        is_disconnected=request.is_disconnected,
        clock=state.clock,
    )
    return StreamingResponse(
        _holding(body, lease),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def _holding(body: AsyncGenerator[bytes], lease: StreamLease | None) -> AsyncGenerator[bytes]:
    """The stream's frames, releasing the caller's slot when it ends: finished, client gone, error,
    or cancelled (a lease that never got here also expires by itself, see ``limits``)."""
    try:
        async with aclosing(body):
            async for frame in body:
                yield frame
    finally:
        if lease is not None:
            lease.release()


@sessions.get(
    "/{session_id}/brief", summary="The current Brief", responses=problems(401, 403, 404, 422)
)
async def get_brief(
    session_id: SessionId, principal: PrincipalDep, state: StateDep, version: Version = None
) -> Brief:
    brief = await state.flow.get_brief(principal, session_id, version)
    if brief is None:
        raise NotFound("There is no Brief yet.")
    return brief


@sessions.get(
    "/{session_id}/plan",
    summary="The Plan view of the current spec",
    responses=problems(401, 403, 404, 422),
)
async def get_plan(
    session_id: SessionId, principal: PrincipalDep, state: StateDep, version: Version = None
) -> PlanView:
    plan = await state.flow.get_plan(principal, session_id, version)
    if plan is None:
        raise NotFound("There is no plan yet.")
    return plan


@sessions.get(
    "/{session_id}/spec",
    summary="APP_SPEC.md of a version",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The spec file, byte for byte. `X-Spec-Hash` is the file hash: sha256 of the "
                "body with its own front-matter `hash:` value blanked (equal to the embedded hash). "
                "Headers: `X-Spec-Version`, `X-Spec-Hash`, `X-Spec-Status`, `X-Spec-Git-Sha` "
                "(once committed) and `ETag`."
            ),
            "content": {"text/markdown": {"schema": {"type": "string"}}},
        },
        **problems(401, 403, 404, 422),
    },
)
async def get_spec(
    session_id: SessionId, principal: PrincipalDep, state: StateDep, version: Version = None
) -> Response:
    spec = await state.flow.get_spec(principal, session_id, version)
    if spec is None:
        raise NotFound("There is no spec yet.")
    headers = {
        "X-Spec-Version": str(spec.version),
        "X-Spec-Hash": spec.hash,
        "X-Spec-Status": spec.status,
        "ETag": f'"{spec.hash}"',
        "Cache-Control": "no-cache",
    }
    if spec.git_sha:
        headers["X-Spec-Git-Sha"] = spec.git_sha
    return Response(
        content=spec.spec_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )


@sessions.get(
    "/{session_id}/versions",
    summary="Brief and spec versions",
    responses=problems(401, 403, 404, 422),
)
async def get_versions(
    session_id: SessionId, principal: PrincipalDep, state: StateDep
) -> VersionsResponse:
    return VersionsResponse(versions=await state.flow.list_versions(principal, session_id))


# --------------------------------------------------------------------------- ops


@ops.get("/healthz", summary="Liveness", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """The process is up. Touches nothing else."""
    return HealthResponse(version=__version__)


@ops.get(
    "/readyz",
    summary="Readiness",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "The database is unreachable"}},
)
async def readyz(request: Request, response: Response) -> ReadyResponse:
    """Ready when the database answers a trivial query and the budget ledger is sound.

    No authentication, so only "ok" or a one-word failure per check: never a reason, a path, a
    number or the budget's state (the spend and the tripwire are `GET /v1/budget`, which is
    authenticated)."""
    checks: dict[str, str] = {}
    state: AppState | None = None
    try:
        state = cast("AppState", await request.app.state.holder.get())
        await state.ping()
        checks["database"] = "ok"
    except Exception:  # any failure to answer means "not ready"
        checks["database"] = "unreachable"
    if state is not None and state.ledger_problem is not None:
        checks["ledger"] = await _ledger_status(state.ledger_problem)
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = 503
    return ReadyResponse(status="ready" if ready else "not_ready", checks=checks)


async def _ledger_status(check: Callable[[], str | None]) -> str:
    """Run the ledger's integrity check off the event loop (it reads the ledger file): "ok", or
    "failed" with the reason in the server log only."""
    try:
        problem = await asyncio.wait_for(asyncio.to_thread(check), timeout=LEDGER_CHECK_SECONDS)
    except Exception:
        log.exception("ledger_check_failed")
        return "failed"
    if problem is not None:
        log.error("ledger_integrity_problem", problem=problem)
        return "failed"
    return "ok"


@dev.get(
    "/budget",
    summary="LLM budget (dev)",
    response_model=BudgetSummary,
    responses=problems(401, 422),
)
async def budget(principal: PrincipalDep, state: StateDep) -> BudgetSummary:
    """Totals of the LLM gateway's budget (mock mode: zero). For development.

    Totals only: no per-scope breakdown (it would list other tenants' session ids and spend) and no
    tripwire. The gateway's own `/v1/budget` is service to service and unchanged."""
    result = state.gateway.budget_status()
    if inspect.isawaitable(result):
        result = await result
    return BudgetSummary(
        cap_micros=result.cap_micros,
        effective_cap_micros=result.effective_cap_micros,
        spent_micros=result.spent_micros,
        reserved_micros=result.reserved_micros,
        live_calls=result.live_calls,
    )
