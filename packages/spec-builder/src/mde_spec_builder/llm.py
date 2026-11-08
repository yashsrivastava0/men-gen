"""How the builder talks to the gateway: one call at a time, never fatal.

Every call of the builder has a deterministic fallback ("no proposals", "pass", the template
summary), so the baseline stands on its own when the model is unavailable, out of budget or returns
something invalid. ``LlmRunner.call`` therefore returns ``None`` instead of raising for every
gateway failure; only cancellation propagates. After the budget is spent or the circuit is open it
stops calling at all (the remaining calls fall back immediately).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from mde_contracts.engines import RunContext
from mde_contracts.gateway import CallContext, CallSpec, GatewayClient
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    GatewayError,
    ValidationFailed,
)

log = logging.getLogger(__name__)

I = TypeVar("I", bound=BaseModel)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741
T = TypeVar("T")


class LlmRunner:
    """Runs call specs through the gateway for one build (scope: the session of the run)."""

    def __init__(self, gateway: GatewayClient, ctx: RunContext) -> None:
        self._gateway = gateway
        self._ctx = ctx
        self.available = True  # False after BudgetExceeded / CircuitOpen
        self.calls = 0
        self.failures = 0
        self.fallbacks: list[str] = []  # call ids that fell back, in order

    def call_context(self, step: str) -> CallContext:
        return CallContext(
            request_id=self._ctx.request_id,
            budget_scope=self._ctx.session_scope,
            purpose=f"build:{step}",
            session_id=self._ctx.session_id,
            tenant_id=self._ctx.tenant_id,
            cancel=self._ctx.cancel,
        )

    async def call(
        self, spec: CallSpec[I, O], input: I, *, step: str, retries: int = 0
    ) -> O | None:
        """The call's output, or ``None`` when the fallback applies. Raises only
        ``CallCancelled``; checks cancellation before every attempt."""
        for attempt in range(retries + 1):
            self._ctx.cancel.check()
            if not self.available:
                break
            try:
                self.calls += 1
                result = await self._gateway.run(spec, input, self.call_context(step))
            except CallCancelled:
                raise
            except (BudgetExceeded, CircuitOpen) as exc:
                self.available = False
                self.failures += 1
                log.warning(
                    "builder: model unavailable, using fallbacks",
                    extra={"call": spec.id, "why": exc.code},
                )
                break
            except (GatewayError, ValidationFailed) as exc:
                self.failures += 1
                log.warning(
                    "builder: call failed",
                    extra={"call": spec.id, "attempt": attempt, "why": exc.code},
                )
                continue
            if result.output is not None:
                return result.output
            self.failures += 1
        self.fallbacks.append(spec.id)
        return None


async def run_all(jobs: Sequence[Callable[[], Awaitable[T]]], concurrency: int) -> list[T]:
    """Run jobs in order (``concurrency`` 1) or a few at a time; results keep the jobs' order, so
    what is applied afterwards never depends on which call finished first."""
    if concurrency <= 1 or len(jobs) <= 1:
        return [await job() for job in jobs]
    gate = asyncio.Semaphore(concurrency)

    async def guarded(job: Callable[[], Awaitable[T]]) -> T:
        async with gate:
            return await job()

    results: list[Any] = await asyncio.gather(*(guarded(j) for j in jobs), return_exceptions=True)
    for item in results:
        if isinstance(item, BaseException):
            raise item
    return list(results)
