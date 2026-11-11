"""One pure-ASGI middleware: request ids, the body size limit and the security headers.

* Request id: a well-formed ``X-Request-Id`` from the client is kept, else ``req_<ulid>``.
* Body limit (default 256 KB, ``COMPASS_MAX_BODY_BYTES``): refused before a byte is read when the declared
  length is too large, else read up to the limit. Upload paths (``.../uploads``) are excluded.
* Security headers on EVERY response (API, SSE, static, errors): CSP, nosniff, no-referrer.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

from starlette.datastructures import Headers, MutableHeaders

from mde_compass.api.errors import REQUEST_ID_HEADER, error_response
from mde_compass.api.headers import CSP, SECURITY_HEADERS, security_headers
from mde_core.ids import new_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,63}$")
_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})


class SecurityAndLimitsMiddleware:
    def __init__(self, app: ASGIApp, *, limit: Callable[[], int]) -> None:
        self.app = app
        self._limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        offered = headers.get(REQUEST_ID_HEADER)
        rid = offered if offered and _CLIENT_REQUEST_ID.match(offered) else new_id("req")
        scope.setdefault("state", {})["request_id"] = rid

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out[REQUEST_ID_HEADER] = rid
                # read per response, so MDE_RESEARCH_ORIGIN takes effect without re-importing
                for key, value in security_headers().items():
                    out[key] = value
            await send(message)

        path = scope.get("path", "")
        limit = self._limit()
        if not path.rstrip("/").endswith("/uploads"):
            declared = headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                await self._too_large(scope, receive, send_secured, limit)
                return
            if scope["method"] in _BODY_METHODS:
                body = await self._read_body(receive, limit)
                if body is None:
                    await self._too_large(scope, receive, send_secured, limit)
                    return
                receive = _replay(body, receive)
        await self.app(scope, receive, send_secured)

    @staticmethod
    async def _read_body(receive: Receive, limit: int) -> bytes | None:
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
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
        response = error_response(
            413, "payload_too_large", f"That request is too large. The limit is {limit} bytes."
        )
        await response(scope, receive, send)


def _replay(body: bytes, receive: Receive) -> Receive:
    served = False

    async def replay() -> Message:
        nonlocal served
        if not served:
            served = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


__all__ = ["CSP", "SECURITY_HEADERS", "SecurityAndLimitsMiddleware"]
