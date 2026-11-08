"""The HTTP client of the Context API (``docs/specs/events-and-api.md``).

One class, :class:`ApiClient`, over an ``httpx.AsyncClient`` that the caller owns: a real network
client for ``mde chat --url ...``, or one over an ASGI transport for ``--local`` (the same code runs
against both). It keeps the dev token and the identity headers to itself: nothing here prints, logs
or puts a token in an exception.

* Every non-2xx answer becomes an :class:`ApiError` carrying the API's own ``code`` and ``message``.
* A connection that cannot be made or does not answer in time becomes :class:`ApiUnreachable`.
* ``POST .../events`` is idempotent by ``event_id``, so a transport failure is retried with the SAME
  event (the API returns the original replies, no second effect).
* :meth:`ApiClient.watch` follows ``GET .../stream`` (server-sent events) with ``Last-Event-ID``
  replay and a hard timeout, so it never hangs. An ASGI transport buffers a whole response, so a
  ``buffered`` client polls ``follow=false`` instead; the caller sees the same typed events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Final, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from mde_contracts.brief import Brief
from mde_contracts.events import OutboundEvent
from mde_contracts.gates import PlanPhase, SessionState
from mde_contracts.gateway import BudgetStatus
from mde_contracts.plan import PlanView
from mde_flow.models import JobSummary, SessionSnapshot, VersionInfo

DEFAULT_URL: Final = "http://127.0.0.1:8100"
REQUEST_TIMEOUT_S: Final = 180.0
CONNECT_TIMEOUT_S: Final = 5.0
POLL_SECONDS: Final = 0.25
TRANSPORT_RETRIES: Final = 2

_EVENT: Final[TypeAdapter[OutboundEvent]] = TypeAdapter(OutboundEvent)
_M = TypeVar("_M", bound=BaseModel)


class _Wire(BaseModel):
    """The client's own view of an API answer: what it reads, nothing it does not (a newer server
    may add fields; an older client ignores them). The CLI never imports the server package."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class CreateSessionResponse(_Wire):
    session: SessionSnapshot
    events: list[OutboundEvent]
    org_profile_saved: bool = False


class EventResponse(_Wire):
    session_id: str
    events: list[OutboundEvent]
    state: SessionState
    plan_phase: PlanPhase | None = None
    job: JobSummary | None = None
    last_seq: int


class VersionsResponse(_Wire):
    versions: list[VersionInfo]


class ApiError(Exception):
    """The API answered with a failure: ``status`` and the stable ``code`` plus a ``message``."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details or []


class ApiUnreachable(Exception):
    """The API could not be reached, or did not answer within the time allowed."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Identity:
    """Who is asking: the three identity headers of the dev authentication."""

    tenant: str
    user: str
    roles: tuple[str, ...] = ()

    def acting_as(self, user: str, roles: tuple[str, ...] = ()) -> Identity:
        """The same tenant, another person (an approver)."""
        return Identity(self.tenant, user, roles)


@dataclass
class SseFrame:
    event: str
    data: str
    id: str | None = None


@dataclass
class SseParser:
    """Line by line server-sent-events parser (``id``, ``event``, ``data``; comments and ``retry``
    are ignored). ``feed`` returns a frame when a blank line closes one."""

    _event: str | None = None
    _id: str | None = None
    _data: list[str] = field(default_factory=lambda: [])

    def feed(self, line: str) -> SseFrame | None:
        line = line.rstrip("\r\n")
        if line == "":
            return self.flush()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id":
            self._id = value
        return None

    def flush(self) -> SseFrame | None:
        if not self._data:
            self._event = self._id = None
            return None
        frame = SseFrame(self._event or "message", "\n".join(self._data), self._id)
        self._event = self._id = None
        self._data = []
        return frame


def events_from_sse_text(text: str) -> list[OutboundEvent]:
    """Typed events of a whole (buffered) ``text/event-stream`` body."""
    parser = SseParser()
    out: list[OutboundEvent] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        frame = parser.feed(line)
        if frame is not None and (ev := _event_of(frame)) is not None:
            out.append(ev)
    if (frame := parser.flush()) is not None and (ev := _event_of(frame)) is not None:
        out.append(ev)
    return out


def _event_of(frame: SseFrame) -> OutboundEvent | None:
    """The typed event of a frame; ``None`` for a type this client does not know (it is skipped, so
    a newer server never breaks an older client). A ``stream_error`` frame raises."""
    if frame.event == "stream_error":
        try:
            body = cast("dict[str, str]", json.loads(frame.data))
        except ValueError:
            body = {}
        raise ApiError(
            200, body.get("code", "stream_error"), body.get("message", "The event stream failed.")
        )
    try:
        return _EVENT.validate_json(frame.data)
    except ValidationError:
        return None


def parse_event(data: Mapping[str, object]) -> OutboundEvent:
    return _EVENT.validate_python(data)


class ApiClient:
    """Typed calls to the Context API for one caller (``identity``)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        identity: Identity,
        *,
        buffered: bool = False,
        label: str = "the API",
        timeout_s: float = REQUEST_TIMEOUT_S,
        poll_s: float = POLL_SECONDS,
    ) -> None:
        self.http = http
        self.identity = identity
        self.buffered = buffered
        self.label = label
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._token = token

    def __repr__(self) -> str:  # never show the token
        return f"ApiClient({self.label!r}, tenant={self.identity.tenant!r}, user={self.identity.user!r})"

    def headers(self, identity: Identity | None = None) -> dict[str, str]:
        who = identity or self.identity
        out = {
            "Authorization": f"Bearer {self._token}",
            "X-Tenant-Id": who.tenant,
            "X-User-Id": who.user,
        }
        if who.roles:
            out["X-Roles"] = ",".join(who.roles)
        return out

    # ----------------------------------------------------------------------------- plumbing

    def _unreachable(self, exc: BaseException) -> ApiUnreachable:
        if isinstance(exc, httpx.ConnectError):
            return ApiUnreachable(
                f"Cannot connect to {self.label}.",
                hint="Start it with `just dev`, point --url at it, or use --local for an "
                "in-process run.",
            )
        return ApiUnreachable(f"{self.label} did not answer in time ({type(exc).__name__}).")

    def _error(self, response: httpx.Response) -> ApiError:
        code = f"http_{response.status_code}"
        message = ""
        request_id: str | None = None
        details: list[dict[str, str]] | None = None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            data = cast("dict[str, Any]", body)
            code = str(data.get("code", code))
            message = str(data.get("message", ""))
            request_id = cast("str | None", data.get("request_id"))
            raw_details = data.get("details")
            if isinstance(raw_details, list):
                details = [
                    {str(k): str(v) for k, v in cast("dict[str, Any]", d).items()}
                    for d in cast("list[Any]", raw_details)
                    if isinstance(d, dict)
                ]
        return ApiError(response.status_code, code, message, request_id=request_id, details=details)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        identity: Identity | None = None,
        json_body: object | None = None,
        params: Mapping[str, str | int | float | bool] | None = None,
        retries: int = TRANSPORT_RETRIES,
        accept: tuple[int, ...] = (200, 201, 202),
    ) -> httpx.Response:
        last: BaseException | None = None
        for _ in range(retries + 1):
            try:
                async with asyncio.timeout(self.timeout_s):
                    response = await self.http.request(
                        method,
                        path,
                        headers=self.headers(identity),
                        json=json_body,
                        params=params,
                        timeout=httpx.Timeout(self.timeout_s, connect=CONNECT_TIMEOUT_S),
                    )
            except (httpx.TransportError, TimeoutError) as exc:
                last = exc
                continue
            if response.status_code not in accept:
                raise self._error(response)
            return response
        assert last is not None
        raise self._unreachable(last)

    # ----------------------------------------------------------------------------- sessions

    async def create_session(
        self,
        mode: str = "guided",
        policy_id: str | None = None,
        org_profile: Mapping[str, object] | None = None,
    ) -> CreateSessionResponse:
        body: dict[str, object] = {"mode": mode}
        if policy_id:
            body["policy_id"] = policy_id
        if org_profile is not None:
            body["org_profile"] = dict(org_profile)
        resp = await self._send("POST", "/v1/sessions", json_body=body)
        return CreateSessionResponse.model_validate_json(resp.content)

    async def snapshot(self, session_id: str, identity: Identity | None = None) -> SessionSnapshot:
        resp = await self._send("GET", f"/v1/sessions/{session_id}", identity=identity)
        return SessionSnapshot.model_validate_json(resp.content)

    async def post_event(
        self, session_id: str, event: Mapping[str, object], identity: Identity | None = None
    ) -> EventResponse:
        resp = await self._send(
            "POST", f"/v1/sessions/{session_id}/events", identity=identity, json_body=dict(event)
        )
        return EventResponse.model_validate_json(resp.content)

    async def stored_events(
        self,
        session_id: str,
        *,
        after: int = 0,
        identity: Identity | None = None,
    ) -> list[OutboundEvent]:
        """Every event stored after ``after`` (the stream with ``follow=false``)."""
        resp = await self._send(
            "GET",
            f"/v1/sessions/{session_id}/stream",
            identity=identity,
            params={"after": after, "follow": False},
        )
        return events_from_sse_text(resp.text)

    async def watch(
        self,
        session_id: str,
        *,
        after: int,
        timeout_s: float,
        identity: Identity | None = None,
    ) -> AsyncGenerator[OutboundEvent, None]:
        """New events as they arrive, for at most about ``timeout_s`` seconds, then the iterator
        ends (the caller loops and reconnects with the cursor it holds). Close it with
        ``contextlib.aclosing`` when you stop early."""
        seconds = max(1.0, timeout_s)
        if self.buffered:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            cursor = after
            while True:
                for event in await self.stored_events(session_id, after=cursor, identity=identity):
                    cursor = max(cursor, event.seq)
                    yield event
                if loop.time() >= deadline:
                    return
                await asyncio.sleep(self.poll_s)
        headers = {
            **self.headers(identity),
            "Accept": "text/event-stream",
            "Last-Event-ID": str(after),
        }
        try:
            async with asyncio.timeout(seconds + 15):
                async with self.http.stream(
                    "GET",
                    f"/v1/sessions/{session_id}/stream",
                    headers=headers,
                    params={"timeout": seconds},
                    timeout=httpx.Timeout(seconds + 15, connect=CONNECT_TIMEOUT_S),
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise self._error(response)
                    parser = SseParser()
                    async for line in response.aiter_lines():
                        frame = parser.feed(line)
                        if frame is not None and (event := _event_of(frame)) is not None:
                            yield event
        except (httpx.TransportError, TimeoutError) as exc:
            raise self._unreachable(exc) from None

    async def next_events(
        self, session_id: str, *, after: int, timeout_s: float, identity: Identity | None = None
    ) -> list[OutboundEvent]:
        """Whatever arrives within ``timeout_s`` (all of it, then the stream closes)."""
        out: list[OutboundEvent] = []
        async with aclosing(
            self.watch(session_id, after=after, timeout_s=timeout_s, identity=identity)
        ) as stream:
            async for event in stream:
                out.append(event)
        return out

    # ----------------------------------------------------------------------------- documents

    async def _model(self, path: str, kind: type[_M], identity: Identity | None = None) -> _M:
        resp = await self._send("GET", path, identity=identity)
        return kind.model_validate_json(resp.content)

    async def brief(self, session_id: str, version: int | None = None) -> Brief:
        return await self._model(self._versioned(session_id, "brief", version), Brief)

    async def plan(self, session_id: str, version: int | None = None) -> PlanView:
        return await self._model(self._versioned(session_id, "plan", version), PlanView)

    async def versions(self, session_id: str) -> VersionsResponse:
        return await self._model(f"/v1/sessions/{session_id}/versions", VersionsResponse)

    async def spec(self, session_id: str, version: int | None = None) -> tuple[str, dict[str, str]]:
        """The ``APP_SPEC.md`` text and the ``X-Spec-*`` headers."""
        path = self._versioned(session_id, "spec", version)
        resp = await self._send("GET", path)
        headers = {k.lower(): v for k, v in resp.headers.items() if k.lower().startswith("x-spec-")}
        return resp.content.decode("utf-8"), headers

    @staticmethod
    def _versioned(session_id: str, tail: str, version: int | None) -> str:
        path = f"/v1/sessions/{session_id}/{tail}"
        return path if version is None else f"{path}?version={version}"

    # ----------------------------------------------------------------------------- ops

    async def readyz(self) -> tuple[int, dict[str, object]]:
        resp = await self._send("GET", "/readyz", accept=(200, 503), retries=0)
        return resp.status_code, cast("dict[str, object]", resp.json())

    async def healthz(self) -> dict[str, object]:
        resp = await self._send("GET", "/healthz", retries=0)
        return cast("dict[str, object]", resp.json())

    async def budget(self) -> BudgetStatus:
        return await self._model("/v1/budget", BudgetStatus)

    async def aclose(self) -> None:
        await self.http.aclose()


def make_http(url: str) -> httpx.AsyncClient:
    """A real network client for ``url`` (no proxies picked up from the environment: it is a local
    development service)."""
    return httpx.AsyncClient(
        base_url=url.rstrip("/"),
        trust_env=False,
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


__all__ = [
    "DEFAULT_URL",
    "ApiClient",
    "ApiError",
    "ApiUnreachable",
    "Identity",
    "SseFrame",
    "SseParser",
    "events_from_sse_text",
    "make_http",
    "parse_event",
]
