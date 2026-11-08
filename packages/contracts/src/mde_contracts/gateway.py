"""LLM Gateway contract types (brief 7.2): call specs, context, result, usage, client protocol.

``CallSpec`` lives here (not in ``mde_prompts``, D-007) because the layering forbids the gateway
from importing prompts. ``mde_prompts`` defines the spec *instances*; the gateway executes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel

from mde_contracts.common import Frozen
from mde_core.cancel import CancellationToken, NeverCancelled

I = TypeVar("I", bound=BaseModel)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741

Route = Literal["fast", "standard", "deep"]
Effort = Literal["none", "low", "medium", "high"]
CallMode = Literal["mock", "replay", "live"]
CallStatus = Literal["ok", "repaired", "cache_hit", "failed"]


class WebSearchTool(Frozen):
    """The only tool allowed (brief 7.7)."""

    kind: Literal["web_search"] = "web_search"
    max_tool_calls: int = 1
    search_context_size: Literal["low", "medium", "high"] = "low"
    allowed_domains: list[str] = []  # may be overridden from policy at call time


ToolSpec = WebSearchTool


@dataclass(frozen=True)
class CallSpec(Generic[I, O]):
    id: str  # "C1.understand"
    version: int  # bump on any prompt or schema change
    input_model: type[I]
    output_model: type[O] | None  # None only for the free-text web search call (S1)
    system_template: str  # file name of a *.jinja under ``template_root``
    user_template: str
    template_root: Path  # the spec's folder: packages/prompts/src/mde_prompts/calls/<call_id>/
    route: Route
    reasoning_effort: Effort
    max_output_tokens: int  # includes reasoning tokens; always set
    mock_handler: str  # dotted path "pkg.module:function"; every spec ships an offline handler
    tools: tuple[ToolSpec, ...] = ()
    untrusted_inputs: tuple[str, ...] = ()  # input fields rendered inside untrusted blocks
    cacheable: bool = False
    repair: bool = True
    fallback: str | None = None  # name of a deterministic fallback the engine registers

    def __post_init__(self) -> None:
        if self.tools and self.untrusted_inputs:
            raise ValueError(f"{self.id}: untrusted inputs must not be combined with tools")
        if self.output_model is None and not self.tools:
            raise ValueError(f"{self.id}: free-text output is only allowed for the web search call")
        if self.max_output_tokens <= 0:
            raise ValueError(f"{self.id}: max_output_tokens must be set")


@dataclass(frozen=True)
class CallContext:
    """Per-call context. ``budget_scope`` is the primary scope; ``extra_scopes`` are nested caps
    that also apply (for example ``research:<run_id>`` inside ``session:<id>``)."""

    request_id: str
    budget_scope: str  # "session:<id>" | "eval:<run>" | "test:<name>"
    purpose: str
    session_id: str | None = None
    tenant_id: str | None = None
    extra_scopes: tuple[str, ...] = ()
    deadline: datetime | None = None
    cancel: CancellationToken = field(default_factory=NeverCancelled)
    route_override: Route | None = None

    @property
    def scopes(self) -> tuple[str, ...]:
        return (self.budget_scope, *self.extra_scopes)


class Usage(Frozen):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens (billed as output)
    reasoning_tokens: int = 0
    tool_calls: int = 0
    cost_micros: int = 0  # integer micro-dollars, rounded up

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            cost_micros=self.cost_micros + other.cost_micros,
        )


class Citation(Frozen):
    url: str
    title: str | None = None
    start: int | None = None
    end: int | None = None


class CallResult(Frozen, Generic[O]):
    call_id: str  # call_...
    spec_id: str
    spec_version: int
    model: str
    mode: CallMode
    status: CallStatus = "ok"
    output: O | None = None  # None for the free-text web search call
    text: str | None = None  # free-text output (S1) or raw text
    citations: list[Citation] = []
    usage: Usage = Usage()
    repaired: bool = False
    cache_hit: bool = False
    latency_ms: int = 0
    request_id: str = ""
    cassette_key: str | None = None
    search_calls: int = 0


class FreeTextResult(Frozen):
    """What the mock handler of the free-text web search call (S1) returns.

    Structured calls' mock handlers return the output model instance directly.
    Mock handler signature (normative): ``def handle(input: I) -> O | FreeTextResult``.
    """

    text: str
    citations: list[Citation] = []
    search_calls: int = 1


class BudgetStatus(Frozen):
    cap_micros: int  # LLM_BUDGET_USD in micros
    effective_cap_micros: int  # 95% of the cap
    spent_micros: int
    reserved_micros: int
    by_scope: dict[str, int] = {}
    tripwire: Literal["ok", "warn_50", "must_run_only_80", "refuse_95"] = "ok"
    live_calls: int = 0


class LlmCallRecord(Frozen):
    """What the gateway persists per call (ledger line and ``llm_call`` row). No prompt text."""

    call_id: str
    request_id: str
    session_id: str | None = None
    tenant_id: str | None = None
    scope: str
    spec_id: str
    spec_version: int
    model: str
    mode: CallMode
    status: CallStatus
    usage: Usage
    latency_ms: int = 0
    repaired: bool = False
    cache_hit: bool = False
    cassette_key: str | None = None
    prompt_hash: str | None = None
    error_code: str | None = None
    at: datetime | None = None


class GatewayClient(Protocol):
    """The only way any package talks to an LLM. Two implementations: in-process and HTTP."""

    async def run(self, spec: CallSpec[I, O], input: I, ctx: CallContext) -> CallResult[O]: ...

    async def budget_status(self) -> BudgetStatus: ...


class LlmCallSink(Protocol):
    """Optional mirror of call records into Postgres (implemented by ``mde_persistence``)."""

    async def record(self, record: LlmCallRecord) -> None: ...
