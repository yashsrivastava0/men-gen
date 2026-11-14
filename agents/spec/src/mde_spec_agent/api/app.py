"""The Spec Bench app: ``create_app()`` serves the API (``/api``) and the static front end (``apps/spec-bench``) on ONE
origin. Loopback only, no sign-in, no cookies, no CORS: a same-origin check refuses state changes from other origins.

The service is composed in the app lifespan by ``build_service`` (live or the gateway's own mock mode: there is no way
to select a test double here). A service is passed in only by tests. Runs orphaned by a previous process become
``partial`` (resumable) and shutdown (SIGTERM included) stops every active run gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mde_core.logging import get_logger
from mde_core.settings import Settings, load_settings
from mde_spec_agent import views
from mde_spec_agent.api.packdata import PackFolder, simulate
from mde_spec_agent.api.security import SameOrigin
from mde_spec_agent.api.sse import event_stream
from mde_spec_agent.config import SpecSettings, load_spec_settings, ui_dir
from mde_spec_agent.service import (
    BadRequest,
    Conflict,
    NotFound,
    SpecService,
    SpecServiceError,
    build_service,
)
from mde_spec_agent.views import packview
from mde_spec_agent.views.load import LoadedRun

MAX_UPLOAD_BODY = 8 * 1024 * 1024 + 1024


def _verify_for_view(ev: dict[str, Any] | None) -> dict[str, Any] | None:
    """Verify writes ``canaries`` as a dict keyed by task id; the G2 view reads a list of canary results."""
    if ev is None or not isinstance(ev.get("canaries"), dict):
        return ev
    return {**ev, "canaries": [ev["canaries"][k] for k in sorted(ev["canaries"])]}


def view_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a view function: a missing screen, flow or role is a plain 404, a refused value a plain 400."""
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise NotFound(
            f"There is no {exc.args[0] if exc.args else 'such item'} in this run."
        ) from None
    except ValueError as exc:
        raise BadRequest(str(exc) or "That request cannot be answered.") from None


MAX_BODY = 256 * 1024
log = get_logger(__name__)


class StartBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    compass_id: str
    research_id: str | None = None
    consumer_profile: str | None = None
    offline: bool = True  # money is never spent unless the page says live


class G15Body(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: str
    moves: list[dict[str, Any]] = []
    everything_in_one_release: bool = False


class G2Body(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: str = "approve"
    content_hash: str = ""
    suggestion_choices: dict[str, bool] | None = None


class ProbeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    offline: bool = True


class BodyLimit:
    """Pure ASGI middleware: refuse bodies over the limit (the upload gets the larger one). A declared length is refused
    before anything is read; a streamed body is cut at the limit and the answer is replaced by the 413."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = MAX_UPLOAD_BODY if scope["path"].rstrip("/") == "/api/inputs/upload" else MAX_BODY
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _too_large(scope, receive, send)
            return
        seen = 0
        exceeded = False

        async def limited() -> Message:
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return msg

        async def guarded(message: Message) -> None:
            if exceeded:
                return  # whatever the app made of a cut body is dropped
            await send(message)

        await self.app(scope, limited, guarded)
        if exceeded:
            await _too_large(scope, receive, send)


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse(
        {"error": {"code": "too_large", "message": "The request is too large."}}, status_code=413
    )(scope, receive, send)


def _problem(code: str, message: str, status: int, fix: str = "") -> JSONResponse:
    err = {"code": code, "message": message}
    if fix:
        err["fix"] = fix
    return JSONResponse({"error": err}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SpecServiceError)
    async def _service(_: Request, exc: SpecServiceError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem("bad_request", "The request is not valid.", 400, "Check the fields sent.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "bad_request", 413: "too_large"}.get(
            exc.status_code, "error"
        )
        return _problem(code, "That request cannot be served.", exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("spec_api_error", error=type(exc).__name__)
        return _problem("internal_error", "Something went wrong.", 500)


def _svc(request: Request) -> SpecService:
    return request.app.state.service  # type: ignore[no-any-return]


def _make_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    # ---- health, budget, profiles -------------------------------------------------------------------------
    @r.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        return _svc(request).health()

    @r.get("/budget")
    async def budget(request: Request) -> dict[str, Any]:
        return _svc(request).budget()

    @r.post("/stop")
    async def stop_all(request: Request) -> dict[str, Any]:
        return {"stopped": await _svc(request).stop_all()}

    @r.get("/profiles")
    async def profiles(request: Request) -> dict[str, Any]:
        return _svc(request).profiles_view()

    @r.get("/config")
    async def config(request: Request) -> dict[str, Any]:
        return _svc(request).config_view()

    # ---- inputs -------------------------------------------------------------------------------------------
    @r.get("/inputs")
    async def inputs(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(_svc(request).inputs)

    @r.get("/inputs/auto")
    async def inputs_auto(request: Request, size: str = "compact") -> dict[str, Any]:
        return await asyncio.to_thread(_svc(request).auto, size)

    @r.post("/inputs/upload", status_code=201)
    async def upload(request: Request) -> dict[str, Any]:
        raw = await request.body()
        return await asyncio.to_thread(_svc(request).upload, raw)

    # ---- runs ---------------------------------------------------------------------------------------------
    @r.post("/preview")
    async def preview_input(body: StartBody, request: Request) -> dict[str, Any]:
        """The zero-cost preview of an input BEFORE a run exists: no run, no model call, no money."""
        return await asyncio.to_thread(
            _svc(request).preview_input,
            compass_id=body.compass_id,
            research_id=body.research_id,
            consumer_profile=body.consumer_profile,
            offline=body.offline,
        )

    @r.post("/runs")
    async def start(body: StartBody, request: Request) -> JSONResponse:
        res = await _svc(request).start_run(
            compass_id=body.compass_id,
            research_id=body.research_id,
            consumer_profile=body.consumer_profile,
            offline=body.offline,
        )
        if res.joined:
            return JSONResponse({"run_id": res.run_id, "joined": True}, status_code=200)
        return JSONResponse({"run_id": res.run_id}, status_code=202)

    @r.get("/runs")
    async def runs(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(_svc(request).list_runs)

    @r.get("/runs/{run_id}")
    async def snapshot(run_id: str, request: Request) -> dict[str, Any]:
        return _svc(request).snapshot(run_id)

    @r.get("/runs/{run_id}/preview")
    async def preview(run_id: str, request: Request) -> dict[str, Any]:
        return _svc(request).preview(run_id)

    @r.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        after: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        service = _svc(request)
        service.get_record(run_id)
        cursor = (
            after
            if after is not None
            else (int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)
        )
        return StreamingResponse(
            event_stream(
                service.store,
                run_id,
                cursor,
                is_active=lambda: service.is_active(run_id),
                disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @r.post("/runs/{run_id}/stop")
    async def stop(run_id: str, request: Request) -> dict[str, Any]:
        return await _svc(request).stop_run(run_id)

    @r.post("/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, request: Request) -> dict[str, Any]:
        return await _svc(request).resume_run(run_id)

    @r.post("/runs/{run_id}/g15")
    async def g15(run_id: str, body: G15Body, request: Request) -> dict[str, Any]:
        return _svc(request).approve_g15(run_id, body.model_dump())

    @r.post("/runs/{run_id}/g2")
    async def g2(run_id: str, body: G2Body, request: Request) -> dict[str, Any]:
        if body.decision != "approve":
            raise BadRequest("decision must be 'approve'.")
        return _svc(request).approve_g2(run_id, body.content_hash, body.model_dump())

    # ---- the pack -----------------------------------------------------------------------------------------
    def folder(request: Request, run_id: str) -> PackFolder:
        service = _svc(request)
        service.get_record(run_id)
        pf = PackFolder(service.store, run_id)
        if not pf.exists:
            raise Conflict("This run has no pack yet.", "Wait for the run to reach G2.")
        return pf

    @r.get("/runs/{run_id}/pack/tree")
    async def pack_tree(run_id: str, request: Request) -> dict[str, Any]:
        pf = folder(request, run_id)
        return await asyncio.to_thread(lambda: packview.pack_tree(pf.pack()))

    @r.get("/runs/{run_id}/pack/file")
    async def pack_file(run_id: str, request: Request, path: str = "") -> PlainTextResponse:
        text = folder(request, run_id).read_file(path)
        if text is None:
            raise NotFound("That file is not part of this pack.")
        return PlainTextResponse(
            text,
            media_type="text/plain",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    @r.get("/runs/{run_id}/pack/manifest")
    async def pack_manifest(run_id: str, request: Request) -> dict[str, Any]:
        return folder(request, run_id).manifest

    @r.get("/runs/{run_id}/pack/tasks")
    async def pack_tasks(run_id: str, request: Request) -> dict[str, Any]:
        pf = folder(request, run_id)
        return await asyncio.to_thread(lambda: packview.tasks_view(pf.pack()))

    @r.get("/runs/{run_id}/pack/budget")
    async def pack_budget(run_id: str, request: Request) -> dict[str, Any]:
        pf = folder(request, run_id)
        return await asyncio.to_thread(lambda: packview.budget_view(pf.pack()))

    @r.get("/runs/{run_id}/pack/checks")
    async def pack_checks(run_id: str, request: Request) -> dict[str, Any]:
        raw = folder(request, run_id).selfcheck()
        if raw is None:
            raise NotFound("This run has no check report yet.")
        return packview.checks_view(raw)

    @r.get("/runs/{run_id}/pack/selfcheck")
    async def pack_selfcheck(run_id: str, request: Request) -> dict[str, Any]:
        out = folder(request, run_id).selfcheck()
        if out is None:
            raise NotFound("This run has no self-check yet.")
        return out

    @r.get("/runs/{run_id}/pack/verify")
    async def pack_verify(run_id: str, request: Request) -> dict[str, Any]:
        out = folder(request, run_id).verify()
        return out if out is not None else {"enabled": False}

    @r.get("/runs/{a}/diff/{b}")
    async def diff(a: str, b: str, request: Request) -> dict[str, Any]:
        pa, pb = folder(request, a), folder(request, b)
        return await asyncio.to_thread(lambda: packview.diff_packs(pa.pack(), pb.pack()))

    # ---- views (pure view-data functions of ``mde_spec_agent.views``) ------------------------------------
    async def loaded(request: Request, run_id: str, *, need_pack: bool = False) -> LoadedRun:
        m = await asyncio.to_thread(_svc(request).load, run_id)
        if need_pack and (m.pack is None or m.check is None):
            raise Conflict(
                "The pack of this run is not built yet.", "Wait for the run to reach G2."
            )
        return m

    @r.get("/runs/{run_id}/views/g15")
    async def view_g15(run_id: str, request: Request) -> Any:
        m = await loaded(request, run_id)
        return view_call(views.g15, m.graph, m.derived)

    @r.get("/runs/{run_id}/views/g2")
    async def view_g2(run_id: str, request: Request) -> Any:
        m = await loaded(request, run_id, need_pack=True)
        rec = _svc(request).get_record(run_id)
        out = view_call(
            views.g2,
            m.graph,
            m.pack,
            m.check,
            m.derived,
            flags=m.flags,
            banners=m.banners,
            rcode_rows=m.rcode_rows,
            verify_report=_verify_for_view(m.verify),
            research=m.research,
        )
        if isinstance(out, dict):
            out.setdefault("content_hash", rec.get("content_hash"))
            needs = _svc(request).missing_needs(run_id)
            if needs:
                out["blocking_needs"] = needs
        return out

    @r.get("/runs/{run_id}/views/screen/{sid}")
    async def view_screen(run_id: str, sid: str, request: Request, profile: str = "typical") -> Any:
        m = await loaded(request, run_id)
        return view_call(views.screen_view, m.graph, sid, profile or "typical")

    @r.get("/runs/{run_id}/views/journey/{flow_id}")
    async def view_journey(
        run_id: str,
        flow_id: str,
        request: Request,
        role: str | None = None,
        profile: str = "typical",
    ) -> Any:
        m = await loaded(request, run_id)
        return view_call(views.journey_view, m.graph, flow_id, role, profile or "typical")

    @r.get("/runs/{run_id}/views/graph")
    async def view_graph(run_id: str, request: Request, role: str | None = None) -> Any:
        m = await loaded(request, run_id)
        return view_call(views.nav_graph, m.graph, role, m.derived)

    @r.get("/runs/{run_id}/views/data")
    async def view_data(run_id: str, request: Request) -> Any:
        m = await loaded(request, run_id)
        return view_call(views.data_view, m.graph)

    @r.get("/runs/{run_id}/views/needs")
    async def view_needs(run_id: str, request: Request) -> Any:
        m = await loaded(request, run_id, need_pack=True)
        return view_call(views.needs_view, m.pack, m.graph)

    @r.get("/runs/{run_id}/export/preview.html")
    async def export_preview(run_id: str, request: Request) -> HTMLResponse:
        m = await loaded(request, run_id, need_pack=True)
        text = await asyncio.to_thread(
            views.preview_html,
            m.pack,
            m.graph,
            derived=m.derived,
            title=f"Spec preview {run_id}",
        )
        return HTMLResponse(
            text,
            headers={
                "Content-Disposition": 'attachment; filename="preview.html"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    # ---- the mock front end: simulator and probe ----------------------------------------------------------
    @r.get("/runs/{run_id}/simulate")
    async def simulate_route(
        run_id: str,
        request: Request,
        mode: str = "all",
        consumer: str | None = None,
        screen: str | None = None,
    ) -> dict[str, Any]:
        if mode not in ("all", "one", "parallel"):
            raise BadRequest("mode must be all, one or parallel.")
        service = _svc(request)
        pf = folder(request, run_id)
        profile = service.consumer(consumer)
        from mde_spec_agent.run.concurrency import compute_k

        k = compute_k(profile, 20_000, 50, service.cfg)
        return await asyncio.to_thread(
            simulate, pf, mode, consumer=profile, screen=screen, cfg=service.cfg, k=k
        )

    @r.post("/runs/{run_id}/probe")
    async def probe(run_id: str, body: ProbeBody, request: Request) -> dict[str, Any]:
        return await _svc(request).probe(run_id, body.task_id, body.offline)

    return r


router = _make_router()


def create_app(
    service: SpecService | None = None,
    *,
    settings: SpecSettings | None = None,
    core: Settings | None = None,
    ui_path: Path | None = None,
) -> FastAPI:
    """The standalone app: API and static front end on one origin. ``service`` is injected only by tests."""
    cfg = settings or load_spec_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            app.state.service = build_service(cfg, core or load_settings())
        app.state.service.recover()
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(
        title="Spec Bench", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = cfg
    if service is not None:
        app.state.service = service
    install_error_handlers(app)
    app.include_router(router)

    folder_ = ui_path or ui_dir(cfg)
    if (folder_ / "index.html").is_file():
        app.mount("/", StaticFiles(directory=folder_, html=True), name="ui")
    app.add_middleware(BodyLimit)
    app.add_middleware(
        SameOrigin
    )  # outermost: headers on everything, the Origin check before any body is read
    return app


def app_factory() -> FastAPI:
    """Uvicorn factory (``--factory``): the real app from the environment."""
    return create_app()


__all__ = ["app_factory", "create_app"]
