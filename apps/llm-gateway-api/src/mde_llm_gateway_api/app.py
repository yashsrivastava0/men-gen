"""The gateway as an internal HTTP service (brief 7.12). Transport and auth only.

Budget, live gating, retries and redaction are enforced inside ``mde_llm_gateway``; this app adds a
bearer token, JSON in/out and a stable error envelope. Routes:

    POST /v1/runs        {call_id, version, input, context} -> CallResult
    GET  /v1/calls       the catalog (id, version, route, effort, tools, untrusted fields, IO schemas)
    GET  /v1/budget      BudgetStatus
    GET  /v1/usage       ?scope=  spend for one scope (or the totals and by-scope table)
    GET  /healthz        liveness (no auth)
    GET  /readyz         readiness only (no auth; no mode, provider or budget detail)

Everything under ``/v1`` needs ``Authorization: Bearer <GATEWAY_SERVICE_TOKEN>``. With no token
configured the service refuses every ``/v1`` request (fail closed).

HTTP callers are untrusted for budget purposes: a caller-supplied ``purpose`` that starts with
``must-run`` (the privilege to keep spending past the 80% tripwire) is rewritten to ``api:<purpose>``
before the call runs. In-process trusted code may still use ``must-run``.
"""

from __future__ import annotations

import hmac
import json
import unicodedata
from typing import Any, cast

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from mde_contracts.common import Frozen
from mde_contracts.gateway import BudgetStatus
from mde_core.errors import ConfigError, MdeError, ValidationFailed
from mde_core.logging import get_logger
from mde_core.settings import Settings, load_settings
from mde_llm_gateway.budget import MUST_RUN_PREFIX
from mde_llm_gateway.errors import GatewayAuthError
from mde_llm_gateway.factory import create_gateway
from mde_llm_gateway.gateway import Gateway
from mde_llm_gateway.registry import AnySpec, SpecRegistry
from mde_llm_gateway.wire import (
    ErrorBody,
    ErrorEnvelope,
    RunContext,
    RunRequest,
    context_from_wire,
    error_to_wire,
)

log = get_logger("mde.llm_gateway_api")


class CatalogEntry(Frozen):
    id: str
    version: int
    route: str
    reasoning_effort: str
    max_output_tokens: int
    tools: list[dict[str, object]]
    untrusted_inputs: list[str]
    cacheable: bool
    repair: bool
    fallback: str | None
    input_schema: dict[str, object]
    output_schema: dict[str, object] | None


class UsageResponse(Frozen):
    scope: str | None
    spent_micros: int
    reserved_micros: int
    cap_micros: int | None
    live_calls: int
    by_scope: dict[str, int] = {}


def untrusted_purpose(purpose: str) -> str:
    """The purpose an HTTP caller may use: ``must-run`` (any case, spacing or width) is stripped."""
    folded = unicodedata.normalize("NFKC", purpose).strip().casefold()
    if folded.startswith(MUST_RUN_PREFIX):
        return f"api:{purpose}"
    return purpose


def untrusted_context(wire: RunContext) -> RunContext:
    safe = untrusted_purpose(wire.purpose)
    if safe == wire.purpose:
        return wire
    log.warning("gateway_api_must_run_stripped", request_id=wire.request_id)
    return wire.model_copy(update={"purpose": safe})


def _default_registry() -> SpecRegistry:
    from mde_prompts.registry import PromptRegistry  # apps may import prompts; the gateway may not

    return cast("SpecRegistry", PromptRegistry())


def catalog_entry(spec: AnySpec) -> CatalogEntry:
    return CatalogEntry(
        id=spec.id,
        version=spec.version,
        route=spec.route,
        reasoning_effort=spec.reasoning_effort,
        max_output_tokens=spec.max_output_tokens,
        tools=[t.model_dump(mode="json") for t in spec.tools],
        untrusted_inputs=list(spec.untrusted_inputs),
        cacheable=spec.cacheable,
        repair=spec.repair,
        fallback=spec.fallback,
        input_schema=cast("dict[str, object]", spec.input_model.model_json_schema()),
        output_schema=(
            cast("dict[str, object]", spec.output_model.model_json_schema())
            if spec.output_model is not None
            else None
        ),
    )


def create_app(
    settings: Settings | None = None,
    registry: SpecRegistry | None = None,
    *,
    gateway: Gateway | None = None,
) -> FastAPI:
    """Build the app. ``gateway`` lets tests inject a gateway with scripted providers; otherwise it
    is created from ``settings`` by :func:`mde_llm_gateway.factory.create_gateway` (so a forbidden or
    keyless live configuration fails at start-up, not on the first call)."""
    the_settings = settings or load_settings()
    the_registry = registry or _default_registry()
    the_gateway = gateway or create_gateway(the_settings, the_registry)
    app = FastAPI(title="Context Engine LLM Gateway", version="1")
    app.state.gateway = the_gateway
    app.state.registry = the_registry

    expected = the_settings.gateway_service_token

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        if expected is None or not expected.get_secret_value():
            raise ConfigError("GATEWAY_SERVICE_TOKEN is not configured: the API refuses all calls")
        scheme, _, supplied = (authorization or "").partition(" ")
        ok = scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode(), expected.get_secret_value().encode()
        )
        if not ok:
            raise GatewayAuthError("missing or invalid bearer token")

    # ------------------------------------------------------------------ errors
    def envelope_response(exc: BaseException) -> JSONResponse:
        status, envelope = error_to_wire(exc)
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return JSONResponse(envelope.model_dump(mode="json"), status_code=status, headers=headers)

    @app.exception_handler(MdeError)
    async def _mde_error(_: Request, exc: MdeError) -> JSONResponse:
        return envelope_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the submitted values back (they may be prompts or secrets): paths and messages only.
        problems = [
            {"loc": [str(p) for p in err["loc"]], "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        body = ErrorEnvelope(
            error=ErrorBody(
                code="validation_failed",
                message="the request body is invalid",
                details={"errors": cast("list[object]", problems)},
            )
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("gateway_api_unhandled", error_type=type(exc).__name__)
        return envelope_response(exc)

    # ------------------------------------------------------------------ routes
    @app.post("/v1/runs", dependencies=[Depends(authenticate)])
    async def post_run(body: RunRequest) -> JSONResponse:
        spec = the_registry.get(body.call_id, body.version)
        try:
            call_input: BaseModel = spec.input_model.model_validate(body.input)
        except ValidationError as exc:
            raise ValidationFailed(
                f"{spec.id}: invalid input: {exc.error_count()} error(s)",
                details={"errors": [_loc(e) for e in exc.errors(include_input=False)]},
            ) from None
        context = context_from_wire(untrusted_context(body.context))
        result = await the_gateway.run(spec, call_input, context)
        return JSONResponse(json.loads(result.model_dump_json()))

    @app.get("/v1/calls", dependencies=[Depends(authenticate)])
    async def get_calls() -> list[CatalogEntry]:
        return [catalog_entry(spec) for spec in the_registry.catalog()]

    @app.get("/v1/budget", dependencies=[Depends(authenticate)])
    async def get_budget() -> BudgetStatus:
        return the_gateway.budget_status()

    @app.get("/v1/usage", dependencies=[Depends(authenticate)])
    async def get_usage(scope: str | None = None) -> UsageResponse:
        status = the_gateway.budget_status()
        if scope is None:
            return UsageResponse(
                scope=None,
                spent_micros=status.spent_micros,
                reserved_micros=status.reserved_micros,
                cap_micros=status.effective_cap_micros,
                live_calls=status.live_calls,
                by_scope=status.by_scope,
            )
        used = the_gateway.guard.usage_for_scope(scope)
        return UsageResponse(
            scope=scope,
            spent_micros=used.spent_micros,
            reserved_micros=used.reserved_micros,
            cap_micros=used.cap_micros,
            live_calls=status.live_calls,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            the_gateway.budget_status()  # reads the ledger: proves it is usable (and intact)
            the_registry.catalog()  # proves the call specs load
        except Exception as exc:
            log.error("gateway_api_not_ready", error_type=type(exc).__name__)
            return JSONResponse({"status": "not_ready"}, status_code=503)
        # Unauthenticated: readiness only. Mode, provider and the budget tripwire stay behind the
        # bearer token (``GET /v1/budget``): an anonymous caller must not learn that the service is
        # billable or how close it is to its cap (security review 2 / D-071).
        return JSONResponse({"status": "ready"})

    return app


def _loc(err: Any) -> dict[str, object]:
    return {"loc": [str(p) for p in err["loc"]], "msg": err["msg"]}
