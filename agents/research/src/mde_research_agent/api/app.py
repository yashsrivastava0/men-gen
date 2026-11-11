"""The Research HTTP API: ``create_app()`` and the router (prefix ``/v1/research``).

Dev auth mirrors Compass (``Authorization: Bearer <token>``, ``X-Tenant-Id``, ``X-User-Id``). The service is built on
first use inside the app lifespan (so importing stays cheap), runs orphaned from a previous process are marked
interrupted at startup, and shutdown cancels live runs. Cross-origin calls from the test console are allowed for the
configured origins only. ``POST /runs`` may carry a whole Compass export (up to 8 MB); every other body is capped at
256 KB.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mde_research_agent import API_PREFIX
from mde_research_agent.api.errors import Unauthorized, install_error_handlers
from mde_research_agent.api.sse import event_stream
from mde_research_agent.config import ResearchSettings, keys_present, load_research_settings
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import ACTIVE_STATUSES

MAX_RUN_BODY = 8 * 1024 * 1024
MAX_BODY = 256 * 1024


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str | None = None
    brief_export: dict[str, Any]


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class BodyLimit:
    """Pure ASGI middleware: refuse bodies over the limit (the run intake gets the larger one)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = (
            MAX_RUN_BODY
            if scope["path"].rstrip("/").endswith("/runs") and scope["method"] == "POST"
            else MAX_BODY
        )
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await JSONResponse(
                {"code": "too_large", "message": "The request is too large."}, status_code=413
            )(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise ValueError("body too large")
            return msg

        try:
            await self.app(scope, limited, send)
        except ValueError:
            await JSONResponse(
                {"code": "too_large", "message": "The request is too large."}, status_code=413
            )(scope, receive, send)


class Caller(BaseModel):
    tenant_id: str
    user_id: str


def get_service(request: Request) -> ResearchService:
    return request.app.state.service  # type: ignore[no-any-return]


async def get_caller(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Caller:
    settings: ResearchSettings = request.app.state.settings
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(
        token.encode(), settings.research_dev_token.get_secret_value().encode()
    ):
        raise Unauthorized
    return Caller(tenant_id=x_tenant_id or "demo", user_id=x_user_id or "demo-user")


Auth = Annotated[Caller, Depends(get_caller)]
Svc = Annotated[ResearchService, Depends(get_service)]


def _make_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/config")
    async def config(_: Auth, service: Svc) -> dict[str, Any]:
        return {
            "mode": service.mode,
            "search_provider": service.search.name if service.search else "none",
            "keys": keys_present(service.settings),
            "model": service.settings.research_user_agent and "gpt-6-luna",
            "checker_vendor": service.checker_vendor,
        }

    @router.post("/runs", status_code=201)
    async def start(body: StartBody, who: Auth, service: Svc) -> dict[str, Any]:
        row = await service.start_run(who.tenant_id, body.program_id, body.brief_export)
        return {
            "run_id": row.run_id,
            "program_id": row.program_id,
            "status": row.status,
            "events_url": f"{API_PREFIX}/runs/{row.run_id}/events",
        }

    @router.get("/runs/{run_id}")
    async def snapshot(run_id: str, who: Auth, service: Svc) -> Any:
        snap = await service.snapshot(who.tenant_id, run_id)
        return snap.model_dump(mode="json", by_alias=True)

    @router.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        who: Auth,
        service: Svc,
        after: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        await service.get_row(who.tenant_id, run_id)
        cursor = (
            after
            if after is not None
            else (int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)
        )

        async def active() -> bool:
            row = await service.store.get_run(who.tenant_id, run_id)
            return row is not None and row.status in ACTIVE_STATUSES

        return StreamingResponse(
            event_stream(
                service.store,
                run_id,
                cursor,
                is_active=active,
                disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/skip", status_code=202)
    async def skip(run_id: str, who: Auth, service: Svc) -> dict[str, Any]:
        row = await service.skip(who.tenant_id, run_id)
        return {"status": row.status}

    @router.post("/runs/{run_id}/targets/{target_id}/cancel", status_code=202)
    async def cancel(run_id: str, target_id: str, who: Auth, service: Svc) -> dict[str, Any]:
        ok = await service.cancel_target(who.tenant_id, run_id, target_id)
        return {"cancelled": ok}

    @router.get("/runs/{run_id}/result")
    async def result(run_id: str, who: Auth, service: Svc) -> Any:
        return JSONResponse(
            await service.result(who.tenant_id, run_id), headers={"Cache-Control": "no-store"}
        )

    @router.get("/programs/{program_id}/latest")
    async def latest(program_id: str, who: Auth, service: Svc) -> Any:
        return JSONResponse(
            await service.latest(who.tenant_id, program_id),
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="research-{program_id}.json"',
            },
        )

    @router.get("/programs/{program_id}/review")
    async def review(program_id: str, who: Auth, service: Svc) -> Any:
        return JSONResponse(
            await service.review(who.tenant_id, program_id), headers={"Cache-Control": "no-store"}
        )

    @router.post("/runs/{run_id}/items/{item_id}/reject")
    async def reject(
        run_id: str, item_id: str, body: RejectBody, who: Auth, service: Svc
    ) -> dict[str, Any]:
        await service.reject_item(who.tenant_id, run_id, item_id, body.reason)
        return {"item_id": item_id, "rejected": True}

    return router


router = _make_router()


def create_app(
    service: ResearchService | None = None,
    *,
    settings: ResearchSettings | None = None,
    memory: bool = True,
) -> FastAPI:
    cfg = settings or load_research_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            from mde_research_agent.compose import build_service

            app.state.service = build_service(memory=memory, settings=cfg)
        await app.state.service.recover()
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(
        title="Research",
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    if service is not None:
        app.state.service = service
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(BodyLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Tenant-Id",
            "X-User-Id",
            "Last-Event-ID",
        ],
        max_age=600,
    )
    return app
