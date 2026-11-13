"""Same-origin protection for the standalone Spec Bench: security headers and a state-change Origin check.

* Every response carries a strict content-security policy (scripts, styles and requests only from the app's own origin;
  no inline script), ``X-Content-Type-Options``, ``Referrer-Policy`` and ``Cache-Control: no-store`` for the API.
* A state-changing request (POST, PUT, PATCH, DELETE) whose ``Origin`` is not the app's own is refused, and so is one the
  browser marks cross-site (``Sec-Fetch-Site``): a page somewhere else cannot start a run and spend money. Requests with
  no ``Origin`` (curl, tests) are not browsers and pass.
* No CORS at all: the app never answers a cross-origin preflight.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SameOrigin:
    """Pure ASGI middleware (streams SSE untouched)."""

    def __init__(self, app: ASGIApp, *, origins: frozenset[str] | None = None) -> None:
        self.app = app
        self.origins = origins  # None: derive from the Host header of each request

    def _own(self, headers: dict[bytes, bytes]) -> frozenset[str]:
        if self.origins is not None:
            return self.origins
        host = headers.get(b"host", b"").decode("latin-1")
        return frozenset({f"http://{host}"}) if host else frozenset()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if scope["method"] in STATE_CHANGING:
            origin = headers.get(b"origin", b"").decode("latin-1")
            site = headers.get(b"sec-fetch-site", b"").decode("latin-1")
            if (origin and origin not in self._own(headers)) or site == "cross-site":
                await _refuse(send)
                return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                present = {k.lower() for k, _ in hdrs}
                extra = [
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ]
                if scope["path"].startswith("/api"):
                    extra.append((b"cache-control", b"no-store"))
                hdrs += [(k, v) for k, v in extra if k not in present]
                message["headers"] = hdrs
            await send(message)

        await self.app(scope, receive, with_headers)


async def _refuse(send: Send) -> None:
    body = (
        b'{"error":{"code":"bad_origin","message":"This request did not come from the Spec Bench page.",'
        b'"fix":"Open the app at its own address and try again."}}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
