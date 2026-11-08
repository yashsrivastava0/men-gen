"""One pure-ASGI middleware: request ids and the request body size limit.

* **Request id.** Every request gets an id (a well-formed ``X-Request-Id`` from the client is kept,
  otherwise ``req_<ulid>``). It is on the response header, in the log context and in the body of a
  500, so a user can quote it.
* **Body limit** (``API_MAX_BODY_BYTES``, default 1 MB). A larger declared ``Content-Length`` is
  refused before a byte is read; a body without one (chunked) is read up to the limit and refused
  when it exceeds it. A body inside the limit is handed to the application unchanged. The check is
  done here, not in a route, so it also covers routes that never read the body.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

import structlog
from starlette.datastructures import Headers, MutableHeaders

from mde_context_api.errors import (
    REQUEST_ID_HEADER,
    ErrorInfo,
    PayloadTooLarge,
    describe,
    error_response,
)
from mde_core.ids import new_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,63}$")
_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})

TOO_LARGE_TEXT: Final = "That request is too large. The limit is {limit} bytes."


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, limit: Callable[[], int]) -> None:
        self.app = app
        self._limit = limit  # read per request: settings are loaded lazily

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        offered = headers.get(REQUEST_ID_HEADER)
        rid = offered if offered and _CLIENT_REQUEST_ID.match(offered) else new_id("req")
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            limit = self._limit()
            declared = headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                await self._too_large(scope, receive, send_with_id, limit)
                return
            if scope["method"] in _BODY_METHODS:
                body = await self._read_body(receive, limit)
                if body is None:
                    await self._too_large(scope, receive, send_with_id, limit)
                    return
                receive = _replay(body, receive)
            await self.app(scope, receive, send_with_id)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

    @staticmethod
    async def _read_body(receive: Receive, limit: int) -> bytes | None:
        """The whole body, or None when it is larger than ``limit``."""
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away: let the app see it
                return b"".join(chunks)
            chunk: bytes = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        info: ErrorInfo = describe(PayloadTooLarge(TOO_LARGE_TEXT.format(limit=limit)))
        response = error_response(info, request_id=scope["state"]["request_id"])
        await response(scope, receive, send)


def _replay(body: bytes, receive: Receive) -> Receive:
    """A ``receive`` that serves the buffered body once, then defers to the real one."""
    served = False

    async def replay() -> Message:
        nonlocal served
        if not served:
            served = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
