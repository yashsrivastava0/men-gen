"""One stable error body for every failure: ``{"code": ..., "message": ...}``.

``mde_core.errors`` (and ``mde_flow.errors``) carry domain meaning; this module maps them to HTTP
statuses and friendly text. Clients branch on ``code``, never on ``message``. An unexpected failure
is a 500 with a ``request_id`` to quote and no stack trace (the trace goes to the log).

| Failure | Status | code |
| --- | --- | --- |
| no or bad credentials | 401 | ``unauthorized`` |
| ``NotFound`` / ``TenantViolation`` (also another tenant's id) | 404 | ``not_found`` |
| ``Forbidden`` (not the owner, not a required approver) | 403 | ``forbidden`` |
| ``Conflict`` | 409 | ``conflict`` |
| ``HashMismatch`` | 409 | ``hash_mismatch`` |
| ``GateError`` | 409 | ``gate_error`` |
| ``InvalidTransition`` | 409 | ``invalid_transition`` |
| ``CallCancelled`` | 409 | ``cancelled`` |
| body too large | 413 | ``payload_too_large`` |
| unsupported upload type (for example a PDF) | 415 | ``unsupported_media_type`` |
| ``ValidationFailed``, request validation | 422 | ``validation_failed`` |
| ``BudgetExceeded`` | 429 | ``budget_exceeded`` |
| too many sessions created (per caller or tenant) | 429 | ``too_many_requests`` (``Retry-After``) |
| too many open event streams for one caller | 429 | ``too_many_streams`` (``Retry-After``) |
| other ``GatewayError`` | 502 | ``gateway_error`` |
| ``HandoffFailed`` | 503 | ``handoff_failed`` |
| ``ConfigError``, ``LiveCallForbidden`` | 500 | ``config_error`` |
| anything else | 500 | ``internal_error`` |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    ConfigError,
    Conflict,
    GateError,
    GatewayError,
    HashMismatch,
    InvalidTransition,
    LiveCallForbidden,
    MdeError,
    NotFound,
    TenantViolation,
    ValidationFailed,
)
from mde_core.logging import get_logger
from mde_flow.errors import Forbidden, HandoffFailed

log = get_logger("mde_context_api")

REQUEST_ID_HEADER: Final = "X-Request-Id"


class ErrorBody(BaseModel):
    """The body of every non-2xx response."""

    code: str
    message: str
    request_id: str | None = None
    details: list[dict[str, str]] | None = None


class ApiError(MdeError):
    """An error that belongs to the HTTP layer; carries its own status."""

    status: int = 400
    headers: dict[str, str]

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.headers = headers or {}


class Unauthorized(ApiError):
    code = "unauthorized"
    status = 401

    def __init__(self, message: str = "Missing or invalid credentials.") -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class TooManyRequests(ApiError):
    """A rate limit was hit (for example session creation). ``Retry-After`` says when to retry."""

    code = "too_many_requests"
    status = 429

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after))})


class TooManyStreams(ApiError):
    """The caller already holds the maximum number of live event streams."""

    code = "too_many_streams"
    status = 429

    def __init__(self, message: str, *, retry_after: int = 5) -> None:
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after))})


class PayloadTooLarge(ApiError):
    code = "payload_too_large"
    status = 413


class UnsupportedMedia(ApiError):
    code = "unsupported_media_type"
    status = 415


@dataclass(frozen=True)
class ErrorInfo:
    status: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict[str, str])
    details: list[dict[str, str]] | None = None


_MAX_MESSAGE = 300

# Friendly fixed texts for failures whose own message is aimed at developers.
HASH_MISMATCH_TEXT: Final = "That version is out of date. Open the latest page and decide again."
GATE_ERROR_TEXT: Final = (
    "You cannot make that decision here. Check that you are signed in as the right person "
    "and that the page is still open."
)
BUDGET_TEXT: Final = (
    "The budget for this app has been reached, so nothing more can run. "
    "Someone who can approve it needs to raise the limit."
)
GATEWAY_TEXT: Final = "The AI service could not answer just now. Please try again in a moment."
HANDOFF_TEXT: Final = (
    "Your approved spec could not be saved just now. Nothing was lost; please try again."
)
CONFIG_TEXT: Final = "The service is not set up correctly. Please tell whoever runs it."
INTERNAL_TEXT: Final = "Something went wrong on our side. Please try again; quote the request id if it keeps happening."
NOT_FOUND_TEXT: Final = "Not found."


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 1] + "…"


def describe(exc: BaseException) -> ErrorInfo:
    """Map an exception to its status, stable code and friendly message."""
    if isinstance(exc, ApiError):
        return ErrorInfo(exc.status, exc.code, _clip(exc.message), dict(exc.headers))
    if isinstance(exc, HashMismatch):  # before GateError: it is one
        return ErrorInfo(409, "hash_mismatch", HASH_MISMATCH_TEXT)
    if isinstance(exc, GateError):
        return ErrorInfo(409, "gate_error", GATE_ERROR_TEXT)
    if isinstance(exc, InvalidTransition):
        return ErrorInfo(409, "invalid_transition", _clip(exc.message))
    if isinstance(exc, Conflict):
        return ErrorInfo(409, "conflict", _clip(exc.message))
    if isinstance(exc, CallCancelled):
        return ErrorInfo(409, "cancelled", "That was cancelled.")
    if isinstance(exc, TenantViolation):
        return ErrorInfo(404, "not_found", NOT_FOUND_TEXT)
    if isinstance(exc, NotFound):
        return ErrorInfo(404, "not_found", _clip(exc.message))
    if isinstance(exc, Forbidden):
        return ErrorInfo(403, "forbidden", _clip(exc.message))
    if isinstance(exc, ValidationFailed):
        return ErrorInfo(422, "validation_failed", _clip(exc.message))
    if isinstance(exc, BudgetExceeded):  # before GatewayError: it is one
        return ErrorInfo(429, "budget_exceeded", BUDGET_TEXT)
    if isinstance(exc, GatewayError):
        return ErrorInfo(502, "gateway_error", GATEWAY_TEXT)
    if isinstance(exc, HandoffFailed):
        return ErrorInfo(503, "handoff_failed", HANDOFF_TEXT, {"Retry-After": "2"})
    if isinstance(exc, ConfigError | LiveCallForbidden):
        return ErrorInfo(500, "config_error", CONFIG_TEXT)
    return ErrorInfo(500, "internal_error", INTERNAL_TEXT)


_HTTP_CODES: Final[dict[int, str]] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "too_many_requests",
}


def request_id_of(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else None


def error_response(
    info: ErrorInfo, request: Request | None = None, *, request_id: str | None = None
) -> JSONResponse:
    rid = request_id or (request_id_of(request) if request is not None else None)
    body = ErrorBody(
        code=info.code,
        message=info.message,
        request_id=rid if info.status >= 500 else None,
        details=info.details,
    )
    headers = dict(info.headers)
    if rid is not None:
        headers[REQUEST_ID_HEADER] = rid
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=info.status, headers=headers
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    """Where and what, never the offending value (it could be a pasted secret)."""
    out: list[dict[str, str]] = []
    for err in exc.errors()[:20]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        out.append({"field": loc, "problem": str(err.get("msg", "invalid"))[:200]})
    return out


def install_error_handlers(app: FastAPI) -> None:
    async def on_mde_error(request: Request, exc: Exception) -> JSONResponse:
        info = describe(exc)
        if info.status >= 500:
            log.error(
                "request_failed",
                code=info.code,
                error=type(exc).__name__,
                path=request.url.path,
                request_id=request_id_of(request),
            )
        return error_response(info, request)

    async def on_validation(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        text_fields = {"text", "comment", "other_text"}
        if any(
            err.get("type") == "string_too_long" and str(err.get("loc", ("",))[-1]) in text_fields
            for err in exc.errors()
        ):
            # An over-long text field (message, upload, comment): 413, never an echo of the value.
            too_long = ErrorInfo(
                413,
                "payload_too_large",
                "That text is too long to send in one piece. Please shorten it or split it up.",
                details=_validation_details(exc),
            )
            return error_response(too_long, request)
        info = ErrorInfo(
            422,
            "validation_failed",
            "The request is not valid. Check the fields listed in details.",
            details=_validation_details(exc),
        )
        return error_response(info, request)

    async def on_http(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        text = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        headers = dict(exc.headers or {})
        info = ErrorInfo(exc.status_code, code, _clip(text), headers)
        return error_response(info, request)

    async def on_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_of(request)
        log.error(
            "unhandled_error",
            error=type(exc).__name__,
            path=request.url.path,
            request_id=rid,
            exc_info=exc,
        )
        return error_response(describe(exc), request)

    app.add_exception_handler(MdeError, on_mde_error)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(StarletteHTTPException, on_http)
    app.add_exception_handler(Exception, on_unexpected)
