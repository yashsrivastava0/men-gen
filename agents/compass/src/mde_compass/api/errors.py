"""Error bodies: every failure is ``ErrorBody {code, message}``; the request id is on a header.

Adapted from the Context API's error mapping (``mde_context_api.errors``), without importing it.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mde_compass.api.headers import security_headers
from mde_compass.api.schemas import ErrorBody
from mde_core import errors as core_errors
from mde_core.logging import get_logger

log = get_logger("mde_compass.api")
REQUEST_ID_HEADER: Final = "X-Request-Id"


class ApiError(Exception):
    """An error with a fixed HTTP status, code and plain message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class Unauthorized(ApiError):
    def __init__(self) -> None:
        super().__init__(401, "unauthorized", "Authentication is required.")


_MAPPING: Final[tuple[tuple[type[Exception], int], ...]] = (
    (core_errors.NotFound, 404),
    (core_errors.TenantViolation, 404),  # another tenant's id looks exactly like a missing one
    (core_errors.HashMismatch, 409),
    (core_errors.GateError, 409),
    (core_errors.InvalidTransition, 409),
    (core_errors.Conflict, 409),
    (core_errors.ValidationFailed, 422),
    (core_errors.BudgetExceeded, 429),
    (core_errors.CircuitOpen, 503),
    (core_errors.ProviderError, 502),
    (core_errors.MdeError, 400),
)


def describe(exc: Exception) -> tuple[int, str, str]:
    """(status, code, message) for an exception; unknown ones are a generic 500."""
    if isinstance(exc, ApiError):
        return exc.status, exc.code, exc.message
    if isinstance(exc, core_errors.MdeError):
        for cls, status in _MAPPING:
            if isinstance(exc, cls):
                return status, exc.code, exc.message or exc.code
    return 500, "internal_error", "Something went wrong on our side. Please try again."


def error_response(status: int, code: str, message: str, request: Request | None = None):
    headers = security_headers()  # also on 500s, which are built outside the user middleware
    rid = getattr(getattr(request, "state", None), "request_id", None) if request else None
    if rid:
        headers[REQUEST_ID_HEADER] = rid
    body = ErrorBody(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


def _summarize(exc: RequestValidationError) -> str:
    """Where it failed and why, never the offending input (it may be user text or a secret)."""
    parts: list[str] = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{loc or 'request'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "The request is not valid."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):  # type: ignore[no-untyped-def]
        return error_response(exc.status, exc.code, exc.message, request)

    @app.exception_handler(core_errors.MdeError)
    async def _mde(request: Request, exc: core_errors.MdeError):  # type: ignore[no-untyped-def]
        status, code, message = describe(exc)
        return error_response(status, code, message, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        return error_response(422, "validation_failed", _summarize(exc), request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail), request)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        log.error("unhandled_error", error_type=type(exc).__name__)
        return error_response(500, "internal_error", describe(exc)[2], request)
