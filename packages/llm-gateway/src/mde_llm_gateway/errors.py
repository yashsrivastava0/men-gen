"""Gateway error classes.

Most errors live in :mod:`mde_core.errors` (shared hierarchy); this module re-exports the ones the
gateway raises and adds the few that only exist here. ``ERROR_STATUS`` is the stable mapping to HTTP
status codes used by the HTTP API and read back by :class:`HttpGatewayClient`.
"""

from __future__ import annotations

from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CassetteMissing,
    CircuitOpen,
    ConfigError,
    GatewayError,
    LedgerIntegrityError,
    LiveCallForbidden,
    MdeError,
    NotFound,
    ProviderError,
    StructuredOutputError,
    UnknownModelPrice,
    UntrustedWithTools,
    ValidationFailed,
)

__all__ = [
    "ERROR_CLASSES",
    "ERROR_STATUS",
    "BudgetExceeded",
    "CallCancelled",
    "CassetteMissing",
    "CircuitOpen",
    "ConfigError",
    "DeadlineExceeded",
    "GatewayAuthError",
    "GatewayError",
    "LedgerIntegrityError",
    "LiveCallForbidden",
    "MdeError",
    "NotFound",
    "ProviderError",
    "StructuredOutputError",
    "UnknownModelPrice",
    "UntrustedWithTools",
    "ValidationFailed",
]


class DeadlineExceeded(GatewayError):
    """``ctx.deadline`` passed before (or while) the call could be made."""

    code = "deadline_exceeded"


class GatewayAuthError(GatewayError):
    """Missing or wrong bearer token on the HTTP API."""

    code = "unauthorized"


ERROR_CLASSES: dict[str, type[MdeError]] = {
    cls.code: cls
    for cls in (
        BudgetExceeded,
        CallCancelled,
        CassetteMissing,
        CircuitOpen,
        ConfigError,
        DeadlineExceeded,
        GatewayAuthError,
        GatewayError,
        LedgerIntegrityError,
        LiveCallForbidden,
        NotFound,
        ProviderError,
        StructuredOutputError,
        UnknownModelPrice,
        UntrustedWithTools,
        ValidationFailed,
    )
}

# Stable error code -> HTTP status (documented in docs/architecture/llm-gateway.md).
ERROR_STATUS: dict[str, int] = {
    "validation_failed": 422,
    "untrusted_with_tools": 422,
    "not_found": 404,
    "unauthorized": 401,
    "live_call_forbidden": 403,
    "budget_exceeded": 402,
    "ledger_integrity": 503,
    "circuit_open": 503,
    "deadline_exceeded": 504,
    "provider_error": 502,
    "structured_output_error": 502,
    "call_cancelled": 499,
    "unknown_model_price": 500,
    "cassette_missing": 500,
    "config_error": 500,
    "gateway_error": 500,
}
