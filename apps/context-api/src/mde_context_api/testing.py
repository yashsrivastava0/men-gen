"""Importable test helpers for the API (D-017: test modules cannot import each other).

* ``make_settings`` - ``ApiSettings`` for tests: mock LLM, a temp projects directory, a fast worker
  and SSE poll, no dotenv file, no key.
* ``build_state`` - ``AppState`` over any ``Store`` (the in-memory fake or an ephemeral Postgres),
  the real Discover and Research engines on the mock gateway and the scripted ``FakeSpecBuilder``.
* ``Api`` - one user's HTTP conversation over an ASGI client: ``create``, ``say``, ``tap``,
  ``decide``, ``run_job``, ``to_gate1`` and friends, returning typed outbound events.

Nothing here is used by production code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar, cast

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import TypeAdapter

from mde_context_api.composition import AppState, build_app_state
from mde_context_api.main import create_app
from mde_context_api.settings import ApiSettings
from mde_contracts.cards import CardInstance
from mde_contracts.events import GateEvent, OutboundEvent
from mde_contracts.ports import FrontendEnginePort, ProjectRepoPort, Store
from mde_core.clock import Clock
from mde_core.ids import new_id

TEST_TOKEN = "test-token"  # noqa: S105
BASE_URL = "http://testserver"
_EVENT = TypeAdapter(OutboundEvent)
E = TypeVar("E", bound=OutboundEvent)


def make_settings(projects_dir: Path, **overrides: Any) -> ApiSettings:
    fields: dict[str, Any] = {
        "llm_mode": "mock",
        "api_dev_token": TEST_TOKEN,
        "projects_dir": projects_dir,
        "spec_builder": "fake",
        "worker_enabled": False,  # tests run jobs inline unless they ask for the worker
        "worker_poll_seconds": 0.05,
        "worker_heartbeat_seconds": 1.0,
        "worker_stop_timeout_seconds": 5.0,
        "sse_poll_seconds": 0.02,
        "sse_max_poll_seconds": 0.1,
        "sse_keepalive_seconds": 0.2,
        "sse_max_seconds": 10.0,
        **overrides,
    }
    return ApiSettings(**fields)


async def build_state(
    settings: ApiSettings,
    store: Store | None = None,
    *,
    project_repo: ProjectRepoPort | None = None,
    frontend: FrontendEnginePort | None = None,
    clock: Clock | None = None,
) -> AppState:
    """The whole service wired for tests. With ``store=None`` the in-memory fake is used.

    ``clock`` replaces the system clock (the abuse limits and the stream timing use it)."""
    if store is None:
        from mde_flow.testing import InMemoryStore

        store = InMemoryStore()
    return await build_app_state(
        settings,
        store=store,
        fake_builder=True,
        project_repo=project_repo,
        frontend=frontend,
        clock=clock,
    )


def headers(
    tenant: str = "tenant-a",
    user: str = "user-1",
    roles: Sequence[str] = (),
    token: str | None = TEST_TOKEN,
) -> dict[str, str]:
    out = {"X-Tenant-Id": tenant, "X-User-Id": user}
    if token is not None:
        out["Authorization"] = f"Bearer {token}"
    if roles:
        out["X-Roles"] = ",".join(roles)
    return out


def make_client(state_or_settings: AppState | ApiSettings) -> httpx.AsyncClient:
    """An ASGI client for a fresh app (no lifespan; use ``app.router.lifespan_context`` for that)."""
    app = (
        create_app(state=state_or_settings)
        if isinstance(state_or_settings, AppState)
        else create_app(state_or_settings)
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


async def eventually(check: Callable[[], Awaitable[bool]], within: float = 20.0) -> None:
    """Poll ``check`` until it is true; fail the test (never hang) after ``within`` seconds."""
    try:
        async with asyncio.timeout(within):
            while not await check():  # noqa: ASYNC110 - polling stored state is the point
                await asyncio.sleep(0.02)
    except TimeoutError:
        raise AssertionError(f"condition not reached within {within} s") from None


def open_units(state: AppState) -> int:
    """Units of work (pool connections) open right now: Postgres pool checkouts, or the in-memory
    store's counter. Zero means nothing is held across an await."""
    from mde_persistence.store import PostgresStore

    store = state.store
    if isinstance(store, PostgresStore):
        return int(store.engine.pool.checkedout())  # type: ignore[attr-defined]
    return int(getattr(store, "open_units", 0))


@asynccontextmanager
async def serve_app(app: FastAPI) -> AsyncIterator[str]:
    """Run ``app`` on a real localhost socket inside the current loop; yields its base URL.

    ASGI test transports buffer a whole response, so real streaming (SSE) needs a real server.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        async with asyncio.timeout(10):
            await task


def parse_events(items: Sequence[object]) -> list[OutboundEvent]:
    return [_EVENT.validate_python(i) for i in items]


def parse_sse(text: str) -> list[dict[str, str]]:
    """SSE frames as ``{"id", "event", "data"}`` dicts (comments and ``retry`` are skipped)."""
    frames: list[dict[str, str]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        frame: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            frame[name] = value.removeprefix(" ")
        if "data" in frame:
            frames.append(frame)
    return frames


class Api:
    """One user's conversation with one session, over HTTP."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        tenant: str = "tenant-a",
        user: str = "user-1",
        roles: Sequence[str] = (),
    ) -> None:
        self.client = client
        self.headers = headers(tenant, user, roles)
        self.session_id = ""
        self.events: list[OutboundEvent] = []
        self.last: httpx.Response | None = None

    # -- plumbing ----------------------------------------------------------------------------
    def as_user(self, user: str, roles: Sequence[str] = (), *, tenant: str = "tenant-a") -> Api:
        """Another caller looking at the same session (an approver, a stranger, another tenant)."""
        other = Api(self.client, tenant=tenant, user=user, roles=roles)
        other.session_id = self.session_id
        other.events = list(self.events)
        return other

    def eid(self) -> str:
        return new_id("evt")

    def url(self, tail: str = "") -> str:
        return f"/v1/sessions/{self.session_id}{tail}"

    async def get(
        self, tail: str = "", *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        self.last = await self.client.get(self.url(tail), headers=headers or self.headers, **kwargs)
        return self.last

    async def create(self, mode: str = "guided", **body: Any) -> dict[str, Any]:
        resp = await self.client.post(
            "/v1/sessions", json={"mode": mode, **body}, headers=self.headers
        )
        self.last = resp
        assert resp.status_code == 201, resp.text
        data = cast("dict[str, Any]", resp.json())
        self.session_id = data["session"]["session_id"]
        self.events.extend(parse_events(data["events"]))
        return data

    async def post(
        self, event: dict[str, Any], *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        self.last = await self.client.post(
            self.url("/events"), json=event, headers=headers or self.headers, **kwargs
        )
        return self.last

    async def send(self, event: dict[str, Any]) -> list[OutboundEvent]:
        resp = await self.post(event)
        assert resp.status_code in (200, 202), resp.text
        out = parse_events(resp.json()["events"])
        self.events.extend(out)
        return out

    # -- events ------------------------------------------------------------------------------
    async def say(self, text: str) -> list[OutboundEvent]:
        return await self.send({"type": "user_message", "event_id": self.eid(), "text": text})

    async def tap(
        self,
        option_id: str | None = None,
        *,
        other_text: str | None = None,
        you_decide: bool = False,
    ) -> list[OutboundEvent]:
        card = (await self.snapshot())["pending_card"]
        assert card is not None, "no pending card"
        return await self.send(
            {
                "type": "option_selected",
                "event_id": self.eid(),
                "card_instance_id": card["instance_id"],
                "option_ids": [option_id] if option_id else [],
                "other_text": other_text,
                "you_decide": you_decide,
            }
        )

    async def tap_recommended(self, text: str = "Blue Basket") -> list[OutboundEvent]:
        """What a user who agrees with every suggestion would tap."""
        card = CardInstance.model_validate((await self.snapshot())["pending_card"]).card
        if card.type == "multi":
            ids = [o.id for o in card.options if o.recommended]
            return await self.send(
                {
                    "type": "option_selected",
                    "event_id": self.eid(),
                    "card_instance_id": (await self.snapshot())["pending_card"]["instance_id"],
                    "option_ids": ids,
                }
            )
        if card.type in ("text", "upload"):
            return await self.tap(other_text=text)
        option = next((o for o in card.options if o.recommended), card.options[0])
        return await self.tap(option.id)

    async def ui(
        self, action: str, target: str | None = None, value: bool | None = None
    ) -> list[OutboundEvent]:
        return await self.send(
            {
                "type": "ui_action",
                "event_id": self.eid(),
                "action": action,
                "target": target,
                "value": value,
            }
        )

    async def decide(
        self,
        gate: str,
        decision: str = "approve",
        *,
        version: int | None = None,
        hash: str | None = None,
        comment: str | None = None,
        role: str | None = None,
        event_id: str | None = None,
    ) -> httpx.Response:
        shown = self.last_gate(gate)  # type: ignore[arg-type]
        assert shown is not None or (version is not None and hash is not None)
        return await self.post(
            {
                "type": "gate_decision",
                "event_id": event_id or self.eid(),
                "gate": gate,
                "decision": decision,
                "version": version if version is not None else shown.version,  # type: ignore[union-attr]
                "hash": hash if hash is not None else shown.hash,  # type: ignore[union-attr]
                "comment": comment,
                "role": role,
            }
        )

    # -- reading -----------------------------------------------------------------------------
    async def snapshot(self) -> dict[str, Any]:
        resp = await self.get()
        assert resp.status_code == 200, resp.text
        return cast("dict[str, Any]", resp.json())

    async def refresh(self) -> list[OutboundEvent]:
        """Everything stored for the session (what the stream would replay)."""
        resp = await self.get("/stream", params={"follow": "false"})
        assert resp.status_code == 200, resp.text
        self.events = parse_events([json.loads(f["data"]) for f in parse_sse(resp.text)])
        return self.events

    def last_gate(self, gate: str | None = None) -> GateEvent | None:
        for ev in reversed(self.events):
            if isinstance(ev, GateEvent) and (gate is None or ev.gate == gate):
                return ev
        return None

    def of_type(self, kind: type[E]) -> list[E]:
        return [e for e in self.events if isinstance(e, kind)]

    # -- shortcuts ---------------------------------------------------------------------------
    async def to_gate1(self, *, limit: int = 20) -> GateEvent:
        """Greeting, a starter chip, then the recommended tap on every card until the Brief."""
        greeting = next(e for e in self.events if e.type == "assistant_message")
        chips = cast("list[str]", getattr(greeting, "chips", []))
        await self.say(chips[0] if chips else "an online shop")
        for _ in range(limit):
            if (await self.snapshot())["state"] == "BRIEF_REVIEW":
                break
            await self.tap_recommended()
        gate = self.last_gate("brief")
        assert gate is not None, "no Gate 1 after the interview"
        return gate

    async def approve(self, gate: str) -> httpx.Response:
        """Press the gate's approve button and keep the replies."""
        resp = await self.decide(gate)
        assert resp.status_code in (200, 202), resp.text
        self.events.extend(parse_events(resp.json()["events"]))
        return resp
