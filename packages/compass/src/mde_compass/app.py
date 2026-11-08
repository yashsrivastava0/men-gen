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

STATIC_DIR = Path(__file__).resolve().parent / "static_ui"

__all__ = ["API_PREFIX", "STATIC_DIR", "create_app", "router"]


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
    """Build the app. ``memory`` (default: env ``COMPASS_MEMORY=1``) picks the in-memory store."""
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

    if STATIC_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    app.add_middleware(SecurityAndLimitsMiddleware, limit=lambda: api_settings.max_body_bytes)
    return app
