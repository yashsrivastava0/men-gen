"""The Compass HTTP app: ``create_app()`` and the ``router`` the Context API can mount later.

``create_app(service)`` always uses the service it is given (tests pass a scripted fake). With no
service it composes the real one lazily, on first use, through ``mde_compass.compose.build_service()``,
so importing and creating the app stays offline and cheap.
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from mde_compass import API_PREFIX
from mde_compass.api.errors import install_error_handlers
from mde_compass.api.middleware import SecurityAndLimitsMiddleware
from mde_compass.api.routes import health_router, router
from mde_compass.api.settings import ApiSettings, load_api_settings
from mde_compass.engine.ports import CompassService

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_DIR_ENV = "MDE_WEB_DIR"
DEFAULT_WEB_DIR = REPO_ROOT / "apps" / "web" / "console"


def web_dir() -> Path:
    """The directory of the global front end (the test console) that ``/ui/`` serves.

    ``MDE_WEB_DIR`` when set (a relative path is resolved against the repository root), else
    ``<repo root>/apps/web/console``. The front end lives outside the agent (Global rules B); this
    package only serves the files and never imports anything from them.
    """
    override = os.environ.get(WEB_DIR_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_WEB_DIR


def _checked_web_dir() -> Path:
    directory = web_dir()
    hint = f"Set {WEB_DIR_ENV} to a directory that holds index.html (default: apps/web/console)."
    if not directory.is_dir():
        raise FileNotFoundError(f"The test console directory does not exist: {directory}. {hint}")
    if not (directory / "index.html").is_file():
        raise FileNotFoundError(
            f"The test console directory has no index.html: {directory}. {hint}"
        )
    return directory


STATIC_DIR = web_dir()  # kept for callers that read it; create_app() calls web_dir() itself

__all__ = ["API_PREFIX", "STATIC_DIR", "WEB_DIR_ENV", "create_app", "router", "web_dir"]


def _default_factory(memory: bool) -> Callable[[], Any]:
    def build() -> Any:
        compose = importlib.import_module("mde_compass.compose")
        fn = compose.build_service
        try:
            takes_memory = "memory" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes_memory = False
        return fn(memory=memory) if takes_memory else fn()

    return build


def create_app(
    service: CompassService | None = None,
    *,
    settings: ApiSettings | None = None,
    memory: bool | None = None,
) -> FastAPI:
    """Build the app. ``memory`` (default: env ``COMPASS_MEMORY=1``) picks the in-memory store.

    Raises ``FileNotFoundError`` when the test console directory (``web_dir()``) or its
    ``index.html`` is missing.
    """
    console_dir = _checked_web_dir()
    api_settings = settings or load_api_settings()
    use_memory = memory if memory is not None else os.environ.get("COMPASS_MEMORY") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        closer = getattr(getattr(app.state, "service", None), "aclose", None)
        if closer is not None:
            await closer()

    app = FastAPI(
        title="Compass API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/compass/openapi.json",
    )
    app.state.api_settings = api_settings
    app.state.service = service
    app.state.service_factory = None if service is not None else _default_factory(use_memory)
    app.state.stream_counts = {}
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(health_router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/", status_code=307)

    app.mount("/ui", StaticFiles(directory=console_dir, html=True), name="ui")
    app.add_middleware(SecurityAndLimitsMiddleware, limit=lambda: api_settings.max_body_bytes)
    return app
