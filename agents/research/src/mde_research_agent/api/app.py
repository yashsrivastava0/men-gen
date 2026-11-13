"""The Research app: ``create_app()`` serves the API (``/v1/research`` and ``/api``) and the static front end on ONE origin.

Dev auth: ``Authorization: Bearer <token>`` (the page gets it from ``/api/session``), ``X-Tenant-Id``, ``X-User-Id``.
The service is composed in the app lifespan by ``compose_app`` (live only: there is no way to select a test double
here), runs orphaned by a previous process become ``interrupted`` with a terminal event, a sweeper frees runs that
stopped reporting, and shutdown cancels live runs. There is no CORS: a same-origin check refuses state changes from
other origins. ``POST /runs`` may carry a whole Compass export (up to 8 MB), an upload up to 25 MB; every other body
is capped at 256 KB.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mde_core.logging import get_logger
from mde_core.settings import Settings, load_settings
from mde_research_agent import API_PREFIX
from mde_research_agent.api.app_routes import make_api_router
from mde_research_agent.api.deps import Auth, Svc
from mde_research_agent.api.errors import install_error_handlers
from mde_research_agent.api.results_routes import make_results_router
from mde_research_agent.api.security import SameOrigin
from mde_research_agent.api.sse import event_stream
from mde_research_agent.config import ResearchSettings, keys_present, load_research_settings, ui_dir
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import ACTIVE_STATUSES

MAX_RUN_BODY = 8 * 1024 * 1024
MAX_BODY = 256 * 1024
MAX_UPLOAD_BODY = 25 * 1024 * 1024 + 1024
log = get_logger(__name__)


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
        path = scope["path"].rstrip("/")
        limit = (
            MAX_UPLOAD_BODY
            if path == "/api/inputs/upload"
            else MAX_RUN_BODY
            if path.endswith("/runs") and scope["method"] == "POST"
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


def _make_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/config")
    async def config(request: Request, _: Auth, service: Svc) -> dict[str, Any]:
        return {
            "mode": service.mode,
            "search_provider": service.search.name if service.search else "none",
            "keys": keys_present(service.settings),
            "model": request.app.state.core.model_for_route("fast"),
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
    async def latest(
        program_id: str,
        who: Auth,
        service: Svc,
        view: Annotated[str, Query(pattern="^(full|compact)$")] = "full",
        apply_decisions: Annotated[int, Query(ge=0, le=1)] = 0,
    ) -> Any:
        """The newest committed ``research.json`` (immutable, hashed). ``view=compact`` is the deterministic compact
        projection Spec reads; ``apply_decisions=1`` shows the owner's rejections as ``withdrawn`` (the file stays as
        committed: it is an overlay)."""
        body = (
            await service.compact(who.tenant_id, program_id)
            if view == "compact"
            else await service.latest_with_decisions(who.tenant_id, program_id)
            if apply_decisions
            else await service.latest(who.tenant_id, program_id)
        )
        return JSONResponse(
            body,
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
    core: Settings | None = None,
    ui_path: Path | None = None,
    sweep_seconds: float = 60.0,
) -> FastAPI:
    """The standalone app: API and static front end on one origin. ``service`` is injected only by tests."""
    cfg = settings or load_research_settings()
    the_core = core or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            from mde_research_agent.compose import compose_app

            app.state.service = compose_app(cfg, the_core)
        # Name lookups and page text extraction both run in the loop's default thread pool (min(32, cpus + 4) workers). A
        # wide run (10 fetch lanes, 16 extraction lanes) can queue name lookups behind long extractions, which shows up as
        # fetch timeouts (first live Big run, 2026-11-13): a larger pool costs nothing when it is not used.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64))
        await app.state.service.recover()
        sweeper = asyncio.create_task(_sweep_loop(app, sweep_seconds))
        # Provider credits: ask Tavily for its own count once at start (a no-op without a key or a client). Never
        # raises and never delays the start.
        credit_check = asyncio.create_task(app.state.service.refresh_credits())
        try:
            yield
        finally:
            sweeper.cancel()
            credit_check.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            with contextlib.suppress(asyncio.CancelledError):
                await credit_check
            await app.state.service.aclose()

    app = FastAPI(
        title="Research",
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.core = the_core
    if service is not None:
        app.state.service = service
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(make_api_router())
    app.include_router(make_results_router())

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    folder = ui_path or ui_dir(cfg)
    if (folder / "index.html").is_file():
        app.mount("/", StaticFiles(directory=folder, html=True), name="ui")
    app.add_middleware(BodyLimit)
    app.add_middleware(
        SameOrigin
    )  # outermost: headers on everything, the Origin check before any body is read
    return app


async def _sweep_loop(app: FastAPI, every: float) -> None:
    """Free the inputs of runs that stopped reporting (``failed: stalled`` with a terminal event)."""
    while True:
        await asyncio.sleep(every)
        try:
            await app.state.service.sweep_stale()
        except Exception:  # the loop must outlive a bad sweep
            log.exception("research_sweep_failed")
