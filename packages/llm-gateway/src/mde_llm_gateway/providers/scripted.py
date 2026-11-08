"""ScriptedProvider: queued or callable outputs keyed by call id, for tests.

Each call id has a FIFO of scripted items; ``set_default`` supplies a fallback (a fixed item or a
callable) used when the queue is empty. Items can be:

* a ``BaseModel`` / ``dict`` (serialised to JSON), a ``str`` (raw text: use it for malformed output),
* an ``Exception`` (raised, for example ``ProviderCallError(status=429, retryable=True)``),
* a :class:`ScriptedResponse` for control over usage, citations, ``incomplete`` status and delays,
* a callable ``(ProviderRequest) -> item``.

Every request is recorded in ``calls``. An empty script raises :class:`ScriptExhausted` (loudly: a
silent fallback would hide a missing scenario step).
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from mde_contracts.gateway import CallMode, Citation, Usage
from mde_core.clock import Clock
from mde_llm_gateway.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ResponseStatus,
    synthetic_usage,
)


class ScriptExhausted(RuntimeError):
    """The test script has no output left for this call id."""


@dataclass(frozen=True)
class ScriptedResponse:
    """A scripted reply with explicit knobs. ``output`` may be a model, dict or raw string."""

    output: BaseModel | dict[str, object] | str
    usage: Usage | None = None  # None: synthetic usage; use ``NO_USAGE`` to report none at all
    citations: list[Citation] = field(default_factory=lambda: [])
    search_calls: int = 0
    status: ResponseStatus = "completed"
    incomplete_reason: str | None = None
    refusal: str | None = None
    delay: float = 0.0  # seconds of (fake) clock time this reply takes


NO_USAGE = Usage(input_tokens=-1)  # sentinel: the response carries no usage at all

ScriptItem = (
    BaseModel
    | dict[str, object]
    | str
    | Exception
    | ScriptedResponse
    | Callable[[ProviderRequest], "ScriptItem"]
)


class ScriptedProvider:
    name = "scripted"

    def __init__(
        self,
        *,
        mode: CallMode = "mock",
        billable: bool = False,
        clock: Clock | None = None,
    ) -> None:
        self.mode: CallMode = mode
        self.billable = billable
        self._clock = clock
        self._queues: dict[str, deque[ScriptItem]] = defaultdict(deque)
        self._defaults: dict[str, ScriptItem] = {}
        self.calls: list[ProviderRequest] = []

    # ------------------------------------------------------------------ scripting
    def queue(self, call_id: str, *items: ScriptItem) -> ScriptedProvider:
        self._queues[call_id].extend(items)
        return self

    def set_default(self, call_id: str, item: ScriptItem) -> ScriptedProvider:
        """Used whenever the queue for ``call_id`` is empty (``"*"`` matches every call id)."""
        self._defaults[call_id] = item
        return self

    def calls_for(self, call_id: str) -> list[ProviderRequest]:
        return [c for c in self.calls if c.spec_id == call_id]

    @property
    def pending(self) -> int:
        return sum(len(q) for q in self._queues.values())

    # ------------------------------------------------------------------ Provider
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        item = self._next(request)
        while callable(item):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        scripted = item if isinstance(item, ScriptedResponse) else ScriptedResponse(output=item)  # type: ignore[arg-type]
        if scripted.delay and self._clock is not None:
            await self._clock.sleep(scripted.delay)
        text = _to_text(scripted.output)
        if scripted.usage is NO_USAGE:
            usage = None
        elif scripted.usage is not None:
            usage = scripted.usage
        else:
            usage = synthetic_usage(request, text, tool_calls=scripted.search_calls)
        return ProviderResponse(
            text=text,
            usage=usage,
            citations=list(scripted.citations),
            search_calls=scripted.search_calls,
            status=scripted.status,
            incomplete_reason=scripted.incomplete_reason,
            refusal=scripted.refusal,
        )

    def _next(self, request: ProviderRequest) -> ScriptItem:
        queue = self._queues.get(request.spec_id)
        if queue:
            return queue.popleft()
        if request.spec_id in self._defaults:
            return self._defaults[request.spec_id]
        if "*" in self._defaults:
            return self._defaults["*"]
        raise ScriptExhausted(f"ScriptedProvider has no scripted output left for {request.spec_id}")


def _to_text(output: BaseModel | dict[str, object] | str) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, BaseModel):
        return output.model_dump_json()
    return json.dumps(output)
