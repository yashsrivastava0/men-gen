"""The FastAPI application.

``create_app()`` takes no arguments and needs nothing: no database, no key, no settings file. All of
that is built lazily (see ``holder.StateHolder``), so the OpenAPI export (``scripts/export_schemas.py``)
and ``uvicorn mde_context_api.main:app`` work offline. Tests pass ``state=`` (a state they built over
the in-memory store or an ephemeral database) or ``state_factory=``.

Run it: ``python -m mde_context_api`` (Windows-safe; see ``mde_context_api.loop``).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mde_context_api import __version__
from mde_context_api.composition import AppState
from mde_context_api.errors import install_error_handlers
from mde_context_api.holder import StateFactory, StateHolder
from mde_context_api.middleware import RequestContextMiddleware
from mde_context_api.routes import dev, ops, sessions
from mde_context_api.settings import ApiSettings

DESCRIPTION = """
The front half of an AI mobile-app builder: talk to it, approve a plain-language Brief (Gate 1),
let it research and write one `APP_SPEC.md`, approve the Plan (Gate 2), and receive `spec_ready`.

Authenticate with `Authorization: Bearer <token>`. In dev mode (local use only, never on a network)
the token is `API_DEV_TOKEN` and the caller declares itself with `X-Tenant-Id`, `X-User-Id` and
`X-Roles`; when the server has `API_TOKENS` the token itself selects the identity and those headers
are ignored. Every failure is `{"code", "message"}`.
"""

_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})

TAGS = [
    {"name": "sessions", "description": "Conversations, events, gates and the produced documents"},
    {"name": "ops", "description": "Liveness and readiness"},
    {"name": "dev", "description": "Development helpers"},
]


def docs_enabled(settings: ApiSettings | None) -> bool:
    """``/docs``, ``/redoc`` and ``/openapi.json`` need no credentials, so they are off by default.

    ``API_DOCS=1`` (or ``ApiSettings(api_docs=True)``) turns them on. Without settings only the
    environment is read: ``create_app()`` must stay free of settings files and databases."""
    if settings is not None:
        return settings.api_docs
    return os.environ.get("API_DOCS", "").lower() in _TRUE  # what pydantic reads as true


def create_app(
    settings: ApiSettings | None = None,
    *,
    state: AppState | None = None,
    state_factory: StateFactory | None = None,
) -> FastAPI:
    holder = StateHolder(settings, state=state, factory=state_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await holder.start()
        try:
            yield
        finally:
            await holder.close()

    docs = docs_enabled(settings if settings is not None else state.settings if state else None)
    app = FastAPI(
        title="Context Engine API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        # no routes for the interactive docs unless asked for; ``app.openapi()`` still works, so
        # the schema is exported offline (scripts/export_schemas.py -> docs/api/openapi.json)
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.holder = holder
    app.add_middleware(RequestContextMiddleware, limit=lambda: holder.settings.max_body_bytes)
    install_error_handlers(app)
    app.include_router(sessions)
    app.include_router(dev)
    app.include_router(ops)
    return app


app = create_app()
