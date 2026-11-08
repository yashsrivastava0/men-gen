"""HTTP wire format shared by the gateway API (server) and ``HttpGatewayClient`` (client).

* ``RunRequest``: ``{call_id, version, input, context}``; ``context`` is the JSON form of a
  ``CallContext`` (the cancellation token does not travel; cancelling an HTTP call means dropping it).
* Success body: ``CallResult`` as JSON (``output`` is the output model's JSON, ``null`` for free text).
* Errors: ``{"error": {"code", "message", "details"}}`` with a stable HTTP status per ``code``
  (``mde_llm_gateway.errors.ERROR_STATUS``). The client turns the envelope back into the SAME
  exception class, so callers cannot tell the two clients apart.
"""

from __future__ import annotations

import json
from typing import cast

from pydantic import AwareDatetime

from mde_contracts.common import Frozen
from mde_contracts.gateway import CallContext, Route
from mde_core.cancel import CancellationToken
from mde_core.errors import GatewayError, MdeError, ProviderError
from mde_llm_gateway.errors import ERROR_CLASSES, ERROR_STATUS


class RunContext(Frozen):
    request_id: str
    budget_scope: str
    purpose: str
    session_id: str | None = None
    tenant_id: str | None = None
    extra_scopes: list[str] = []
    deadline: AwareDatetime | None = None
    route_override: Route | None = None


class RunRequest(Frozen):
    call_id: str
    version: int | None = None
    input: dict[str, object]
    context: RunContext


class ErrorBody(Frozen):
    code: str
    message: str
    details: dict[str, object] = {}


class ErrorEnvelope(Frozen):
    error: ErrorBody


def context_to_wire(ctx: CallContext) -> RunContext:
    return RunContext(
        request_id=ctx.request_id,
        budget_scope=ctx.budget_scope,
        purpose=ctx.purpose,
        session_id=ctx.session_id,
        tenant_id=ctx.tenant_id,
        extra_scopes=list(ctx.extra_scopes),
        deadline=ctx.deadline,
        route_override=ctx.route_override,
    )


def context_from_wire(wire: RunContext, cancel: CancellationToken | None = None) -> CallContext:
    kwargs: dict[str, object] = {}
    if cancel is not None:
        kwargs["cancel"] = cancel
    return CallContext(
        request_id=wire.request_id,
        budget_scope=wire.budget_scope,
        purpose=wire.purpose,
        session_id=wire.session_id,
        tenant_id=wire.tenant_id,
        extra_scopes=tuple(wire.extra_scopes),
        deadline=wire.deadline,
        route_override=wire.route_override,
        **kwargs,  # type: ignore[arg-type]
    )


def _json_safe(value: dict[str, object]) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(json.dumps(value, default=str)))


def error_to_wire(exc: BaseException) -> tuple[int, ErrorEnvelope]:
    """``(http_status, envelope)`` for any exception. Unknown exceptions leak nothing."""
    if not isinstance(exc, MdeError):
        body = ErrorBody(code="gateway_error", message="internal gateway error")
        return 500, ErrorEnvelope(error=body)
    details = _json_safe(exc.details)
    if isinstance(exc, ProviderError):
        details = {
            **details,
            "provider_status": exc.status,
            "retryable": exc.retryable,
            "retry_after": exc.retry_after,
        }
    status = ERROR_STATUS.get(exc.code, 500)
    return status, ErrorEnvelope(
        error=ErrorBody(code=exc.code, message=exc.message, details=details)
    )


def error_from_wire(status: int, envelope: ErrorEnvelope) -> MdeError:
    """Rebuild the exception the server raised (same class, message and details)."""
    body = envelope.error
    cls = ERROR_CLASSES.get(body.code)
    if cls is None:
        return GatewayError(body.message, details={**body.details, "http_status": status})
    if issubclass(cls, ProviderError):
        details = dict(body.details)
        provider_status = details.pop("provider_status", None)
        retryable = details.pop("retryable", False)
        retry_after = details.pop("retry_after", None)
        return cls(
            body.message,
            status=provider_status if isinstance(provider_status, int) else None,
            retryable=bool(retryable),
            retry_after=float(retry_after) if isinstance(retry_after, (int, float)) else None,
            details=details,
        )
    return cls(body.message, details=body.details)
