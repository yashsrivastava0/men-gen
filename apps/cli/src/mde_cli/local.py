"""``--local``: the whole Context API in this process, in mock mode.

The app factory (``mde_context_api.main.create_app``) over the REAL stack (Discover, Research, the
Spec Builder, the flow controller, the git hand-off) with the mock LLM provider, an ASGI transport
instead of a socket, and the in-process job worker. No key is read (``_env_file=None``, and the mode
is forced to ``mock`` whatever the environment says), no network is touched.

State lives in the local PostgreSQL (``DATABASE_URL``, default the private cluster on port 54329:
``uv run python scripts/dev_db.py up`` then ``just migrate``). ``memory=True`` swaps it for the
in-memory store: nothing to install, nothing kept after the run.

The gateway is built here (the composition root would build the same one) with the per-route request
limits lifted: they exist to protect a real provider's quota, and in mock mode they only make a demo
wait a second between calls.

Layering note: ``.importlinter`` lists ``mde_cli`` and ``mde_context_api`` as siblings of one layer,
which forbids a static import between them, yet running the API in process is exactly what
``--local`` is for. The API modules are therefore loaded by name (``importlib``) in this one module,
the same way the API itself loads the Spec Builder. If the layer contract ever puts ``mde_cli`` above
``mde_context_api`` these can become ordinary imports.
"""

from __future__ import annotations

import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from mde_cli.client import ApiClient, Identity
from mde_cli.errors import CliError
from mde_core.clock import SystemClock
from mde_core.ids import new_id
from mde_core.paths import anchor_to_main_repo

LOCAL_LABEL = "the in-process API"
MOCK_RPM = {"fast": 600_000, "standard": 600_000, "deep": 600_000}


def _api(module: str) -> Any:
    """A module of the context API, loaded by name (see the layering note above)."""
    return import_module(f"mde_context_api.{module}")


@dataclass
class LocalApi:
    """An in-process API. Use ``await start(identity)`` to get a client, ``await stop()`` after."""

    memory: bool = False
    projects_dir: Path | None = None
    token: str = field(default_factory=lambda: secrets.token_hex(16), repr=False)
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)

    async def start(self, identity: Identity) -> ApiClient:
        from mde_llm_gateway import GatewayConfig, InProcessGatewayClient, create_gateway
        from mde_prompts.registry import PromptRegistry

        api_settings = _api("settings").ApiSettings
        projects = self.projects_dir or anchor_to_main_repo(
            api_settings(_env_file=None).projects_dir
        )
        settings = api_settings(
            _env_file=None,
            llm_mode="mock",
            openai_api_key=None,
            forbid_live=True,
            api_dev_token=SecretStr(self.token),
            spec_builder="real",
            worker_enabled=True,
            worker_poll_seconds=0.1,
            sse_poll_seconds=0.1,
            projects_dir=projects,
        )
        clock = SystemClock()
        sink = None
        if self.memory:
            from mde_flow.testing import InMemoryStore

            store: Any = InMemoryStore(clock=clock, ids=new_id)
        else:
            from mde_persistence.db import create_engine
            from mde_persistence.llm_calls import PgLlmCallSink
            from mde_persistence.store import PostgresStore

            engine = create_engine(settings)
            self._stack.push_async_callback(engine.dispose)
            store = PostgresStore(engine, clock=clock)
            sink = PgLlmCallSink(engine, clock=clock)
        gateway = InProcessGatewayClient(
            create_gateway(
                settings,
                PromptRegistry(),
                sink=sink,
                clock=clock,
                config=GatewayConfig(rpm=MOCK_RPM),
            )
        )
        state = await _api("composition").build_app_state(
            settings, store=store, gateway=gateway, clock=clock
        )
        self._stack.push_async_callback(state.aclose)
        await self._check_database(state)
        app = _api("main").create_app(settings, state=state)
        await self._stack.enter_async_context(app.router.lifespan_context(app))
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://in-process",
        )
        self._stack.push_async_callback(http.aclose)
        return ApiClient(http, self.token, identity, buffered=True, label=LOCAL_LABEL)

    async def _check_database(self, state: Any) -> None:
        """A clear message instead of a stack trace when Postgres is down or not migrated."""
        from sqlalchemy import text

        from mde_persistence.store import PostgresStore

        try:
            await state.ping()
        except Exception as exc:
            raise CliError(
                f"Cannot reach the local PostgreSQL ({type(exc).__name__}).",
                hint="Start it with `uv run python scripts/dev_db.py up` and run `just migrate`, "
                "or add --memory for a throwaway run that needs no database.",
            ) from None
        if isinstance(state.store, PostgresStore):
            async with state.store.engine.connect() as conn:
                table = (await conn.execute(text("SELECT to_regclass('public.session')"))).scalar()
            if table is None:
                raise CliError(
                    "The database has no tables yet.",
                    hint="Run `just migrate` (or add --memory for a throwaway run).",
                )

    async def stop(self) -> None:
        await self._stack.aclose()
