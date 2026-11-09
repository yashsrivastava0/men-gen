"""All ``/v1/compass`` endpoints (prompt 5.7). Thin: auth, parse, delegate to the ``CompassService``
held in ``app.state.service``, shape the reply. Control flow lives in the service, not here.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import JsonValue

from mde_compass.api.auth import CallerDep, caller_dependency
from mde_compass.api.errors import ApiError
from mde_compass.api.schemas import (
    CreateSessionRequest,
    EventAck,
    SessionList,
    SessionSummary,
)
from mde_compass.api.settings import ApiSettings
from mde_compass.api.sse import (
    event_stream,
    parse_last_event_id,
)
from mde_compass.catalog.loader import SUPPORTED_LANGS, load_catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.events import GateDecision, InboundEvent
from mde_compass.domain.session import SessionRecord
from mde_compass.engine.ports import Caller, CompassService

router = APIRouter(prefix="/v1/compass", tags=["compass"])
health_router = APIRouter(tags=["health"])

NO_STORE = {"Cache-Control": "no-store"}
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


async def get_service(request: Request) -> CompassService:
    """The service for this request (composed lazily by ``create_app`` when none was passed in)."""
    holder = request.app.state
    service = getattr(holder, "service", None)
    if service is None:
        builder = getattr(holder, "service_factory", None)
        if builder is None:
            raise ApiError(503, "not_ready", "The service is not available.")
        try:
            service = builder()
            if inspect.isawaitable(service):
                service = await service
        except ModuleNotFoundError as exc:  # the engine composition module is not there yet
            if exc.name != "mde_compass.compose":
                raise
            raise ApiError(
                503, "not_ready", "The Compass engine is not composed yet (mde_compass.compose)."
            ) from exc
        holder.service = service
    return service  # type: ignore[no-any-return]


ServiceDep = Annotated[CompassService, Depends(get_service)]


def _usd(micros: int) -> str:
    return f"{(Decimal(micros) / Decimal(1_000_000)).quantize(Decimal('0.0001'))}"


def to_summary(rec: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=rec.id,
        title=rec.title,
        user_label=rec.user_label,
        mode=rec.mode,
        locale=rec.locale,
        state=rec.state,
        brief_version=rec.brief_version,
        approved_version=rec.approved_version,
        spent_usd=_usd(rec.spent_micros),
        llm_calls=rec.llm_calls,
        created_at=rec.created_at.isoformat() if rec.created_at else None,
        updated_at=rec.updated_at.isoformat() if rec.updated_at else None,
    )


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _accepts(fn: Any, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# --- sessions --------------------------------------------------------------------------------------


@router.post("/sessions", status_code=201)
async def create_session(
    body: CreateSessionRequest, caller: CallerDep, service: ServiceDep
) -> SessionSummary:
    rec = await service.create_session(
        caller, mode=body.mode, locale=body.locale, user_label=body.user_label
    )
    return to_summary(rec)


@router.get("/sessions")
async def list_sessions(caller: CallerDep, service: ServiceDep) -> SessionList:
    return SessionList(sessions=[to_summary(r) for r in await service.list_sessions(caller)])


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, caller: CallerDep, service: ServiceDep) -> SessionSummary:
    return to_summary(await service.get_session(caller, session_id))


@router.post("/sessions/{session_id}/events")
async def post_event(
    session_id: str, event: InboundEvent, caller: CallerDep, service: ServiceDep
) -> EventAck:
    result = await service.handle_event(caller, session_id, event)
    return EventAck(accepted=result.accepted, duplicate=result.duplicate, events=result.events)


@router.get("/sessions/{session_id}/stream")
async def stream(
    request: Request,
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    after: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    follow: bool = True,
) -> StreamingResponse:
    settings: ApiSettings = request.app.state.api_settings
    cursor = parse_last_event_id(request.headers.get("last-event-id"))
    if cursor is None:
        cursor = after
    # read the first batch BEFORE the response starts: an unknown session is a clean 404
    first = await service.list_outbound(caller, session_id, after_seq=cursor, limit=200)
    release = _acquire_stream(request, caller, settings)

    async def frames():  # type: ignore[no-untyped-def]
        try:
            async for frame in event_stream(
                service,
                caller,
                session_id,
                after_seq=cursor,
                poll_seconds=settings.sse_poll_seconds,
                max_poll_seconds=settings.sse_max_poll_seconds,
                keepalive_seconds=settings.sse_keepalive_seconds,
                max_seconds=settings.sse_max_seconds,
                follow=follow,
                first=first,
                is_disconnected=request.is_disconnected,
            ):
                yield frame
        finally:
            release()

    return StreamingResponse(frames(), media_type="text/event-stream", headers=SSE_HEADERS)


def _acquire_stream(request: Request, caller: Caller, settings: ApiSettings):  # type: ignore[no-untyped-def]
    """Per-caller stream cap; returns an idempotent release function."""
    counts: dict[tuple[str, str], int] = request.app.state.stream_counts
    key = (caller.tenant_id, caller.user_id)
    if counts.get(key, 0) >= settings.max_streams_per_caller:
        raise ApiError(429, "too_many_streams", "Too many open streams. Close one and retry.")
    counts[key] = counts.get(key, 0) + 1
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            counts[key] = max(0, counts.get(key, 1) - 1)

    return release


@router.post("/sessions/{session_id}/uploads", response_model=EventAck)
async def uploads(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File()],
) -> EventAck:
    """One file per request (multipart). The byte cap is enforced while reading, never after."""
    limit = DEFAULT_CONFIG.intake.max_file_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(
            413, "too_large", "That file is larger than 15 MB. Please send a smaller one."
        )
    result = await service.ingest_upload(caller, session_id, file.filename or "upload", data)
    return EventAck(accepted=result.accepted, duplicate=result.duplicate, events=result.events)


# --- the Brief ---------------------------------------------------------------------------------------


@router.get("/sessions/{session_id}/brief")
async def get_brief(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> JSONResponse:
    return _json(await service.get_brief(caller, session_id, version))


@router.get("/sessions/{session_id}/brief/view")
async def brief_view(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
    lang: Annotated[str | None, Query(pattern=r"^[a-z]{2}$")] = None,
) -> JSONResponse:
    if lang is not None and lang not in SUPPORTED_LANGS:
        raise ApiError(422, "validation_failed", "Unsupported language.")
    kwargs: dict[str, Any] = {"version": version}
    if _accepts(service.brief_view, "language"):
        kwargs["language"] = lang or (await service.get_session(caller, session_id)).locale
    return _json(await service.brief_view(caller, session_id, **kwargs))


@router.get("/sessions/{session_id}/brief/versions")
async def brief_versions(session_id: str, caller: CallerDep, service: ServiceDep) -> JSONResponse:
    return _json({"versions": await service.brief_versions(caller, session_id)})


@router.get("/sessions/{session_id}/brief/diff")
async def brief_diff(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    from_: Annotated[int, Query(alias="from", ge=1)],
    to: Annotated[int, Query(ge=1)],
) -> JSONResponse:
    return _json(await service.brief_diff(caller, session_id, from_version=from_, to_version=to))


@router.get("/sessions/{session_id}/history")
async def history(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    slot: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> JSONResponse:
    return _json(
        await service.history(caller, session_id, after_idx=after, limit=limit, slot=slot, q=q)
    )


@router.get("/sessions/{session_id}/provenance/{item_id:path}")
async def provenance(
    session_id: str, item_id: str, caller: CallerDep, service: ServiceDep
) -> JSONResponse:
    return _json(await service.provenance(caller, session_id, item_id))


@router.post("/sessions/{session_id}/gate")
async def gate(
    session_id: str, decision: GateDecision, caller: CallerDep, service: ServiceDep
) -> JSONResponse:
    return _json(await service.gate(caller, session_id, decision))


@router.get("/sessions/{session_id}/handoff")
async def handoff(session_id: str, caller: CallerDep, service: ServiceDep) -> JSONResponse:
    return _json(await service.handoff(caller, session_id))


@router.get("/sessions/{session_id}/calls")
async def calls(
    session_id: str,
    caller: CallerDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> JSONResponse:
    return _json({"calls": await service.calls(caller, session_id, limit=limit)})


# --- dev + chrome -------------------------------------------------------------------------------------


@router.get("/budget")
async def budget(request: Request, caller: CallerDep, service: ServiceDep) -> JSONResponse:
    """Dev only: spend against the Compass cap. Hidden (404) when token auth is configured."""
    if request.app.state.api_settings.token_mode:
        raise ApiError(404, "not_found", "Not found.")
    data: dict[str, JsonValue] = await service.budget()
    return _json(data)


@router.get("/strings/{lang}")
async def strings(lang: str) -> JSONResponse:
    """UI chrome strings from the catalog (en | hi); public, no tenant data."""
    if lang not in SUPPORTED_LANGS:
        raise ApiError(404, "not_found", "Unknown language.")
    return JSONResponse(
        {"language": lang, "strings": load_catalog().strings[lang]},
        headers={"Cache-Control": "public, max-age=300"},
    )


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz(request: Request) -> Response:
    ready = getattr(request.app.state, "service", None) is not None or (
        getattr(request.app.state, "service_factory", None) is not None
    )
    return JSONResponse(
        {"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503
    )


__all__ = ["caller_dependency", "health_router", "router"]
