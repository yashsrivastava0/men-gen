"""Shared exception hierarchy. Engines raise these; the API layer maps them to HTTP codes."""

from __future__ import annotations


class MdeError(Exception):
    """Base class. ``code`` is a stable machine-readable string."""

    code: str = "mde_error"

    def __init__(self, message: str = "", *, details: dict[str, object] | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details: dict[str, object] = details or {}


# --- configuration / safety -------------------------------------------------
class ConfigError(MdeError):
    code = "config_error"


class LiveCallForbidden(MdeError):
    """A live LLM call was attempted without LLM_MODE=live + ALLOW_LIVE_LLM=1, or under tests."""

    code = "live_call_forbidden"


class SecretLeakError(MdeError):
    code = "secret_leak"


# --- gateway ----------------------------------------------------------------
class GatewayError(MdeError):
    code = "gateway_error"


class BudgetExceeded(GatewayError):
    code = "budget_exceeded"


class LedgerIntegrityError(BudgetExceeded):
    """The budget ledger is missing, truncated or unreadable although its guard file says it once
    held more: a fresh budget must never appear by deleting a file. Reset explicitly with
    ``python -m mde_llm_gateway.budget_cli init --force`` (or ``MDE_BUDGET_LEDGER_RESET=1``)."""

    code = "ledger_integrity"


class UnknownModelPrice(GatewayError):
    """No price on file for the model: the call is refused (brief 7.9)."""

    code = "unknown_model_price"


class ProviderError(GatewayError):
    """Provider failure. ``retryable`` is True only for 429, 5xx, timeouts and connection errors."""

    code = "provider_error"

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class CircuitOpen(GatewayError):
    code = "circuit_open"


class StructuredOutputError(GatewayError):
    """Output still invalid after the single repair attempt."""

    code = "structured_output_error"


class CassetteMissing(GatewayError):
    code = "cassette_missing"


class UntrustedWithTools(GatewayError):
    """A call spec combines untrusted inputs with tools (hard error, brief 7.3)."""

    code = "untrusted_with_tools"


class CallCancelled(MdeError):
    code = "call_cancelled"


# --- domain -----------------------------------------------------------------
class NotFound(MdeError):
    code = "not_found"


class Conflict(MdeError):
    code = "conflict"


class TenantViolation(MdeError):
    code = "tenant_violation"


class InvalidTransition(MdeError):
    code = "invalid_transition"


class GateError(MdeError):
    code = "gate_error"


class HashMismatch(GateError):
    """An approval was attempted against a stale version hash."""

    code = "hash_mismatch"


class ValidationFailed(MdeError):
    code = "validation_failed"
