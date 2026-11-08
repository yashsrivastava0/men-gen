"""Provider interface: what the pipeline hands to a provider and what comes back.

A provider makes exactly ONE model call (no retries, no repair, no budget logic: the gateway owns
those). It returns raw text plus the usage the API reported, or raises :class:`ProviderCallError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from mde_contracts.gateway import CallMode, Citation, Effort, ToolSpec, Usage
from mde_core.errors import ProviderError
from mde_llm_gateway.registry import AnySpec

CallKind = Literal["main", "escalate", "repair"]
ResponseStatus = Literal["completed", "incomplete"]


@dataclass(frozen=True)
class ProviderRequest:
    """One fully rendered model call."""

    spec: AnySpec
    input: BaseModel  # the validated input (the mock handler needs it)
    model: str
    system: str
    user: str
    effort: Effort
    max_output_tokens: int
    tools: tuple[ToolSpec, ...]
    schema: dict[str, object] | None  # strict JSON schema; None for free text
    schema_name: str
    schema_hash: str
    timeout_s: float
    attempt: int = 1
    kind: CallKind = "main"

    @property
    def spec_id(self) -> str:
        return self.spec.id


@dataclass(frozen=True)
class ProviderResponse:
    """The raw outcome of one call.

    ``usage`` is what the API reported (``None`` if it reported nothing: the gateway then books the
    reserved worst case). ``search_calls`` is the number of billable web-search calls.
    """

    text: str
    usage: Usage | None
    citations: list[Citation] = field(default_factory=lambda: [])
    search_calls: int = 0
    status: ResponseStatus = "completed"
    incomplete_reason: str | None = None
    refusal: str | None = None
    cassette_key: str | None = None
    response_id: str | None = None


class ProviderCallError(ProviderError):
    """A provider failure with what is known about its cost.

    * ``usage`` is set when the API reported usage for the failed response (it is committed);
    * ``maybe_billed`` is True for timeouts and dropped connections: the request may have been
      processed, so the gateway books the estimated input cost instead of releasing for free.
    """

    code = "provider_error"

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        usage: Usage | None = None,
        maybe_billed: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            status=status,
            retryable=retryable,
            retry_after=retry_after,
            details=details,
        )
        self.usage = usage
        self.maybe_billed = maybe_billed


class Provider(Protocol):
    """Anything that can answer one :class:`ProviderRequest`."""

    name: str
    mode: CallMode  # reported on ``CallResult.mode``
    billable: bool  # True only for real (or recorded-live) calls: they use the shared ledger

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def synthetic_usage(request: ProviderRequest, output_text: str, *, tool_calls: int = 0) -> Usage:
    """Small deterministic usage for offline providers (about 4 characters per token)."""
    return Usage(
        input_tokens=max(1, (len(request.system) + len(request.user)) // 4),
        output_tokens=max(1, len(output_text) // 4),
        tool_calls=tool_calls,
    )
