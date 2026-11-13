"""One place that runs a structured model call, validates it, falls back and counts failures.

Same shape as Compass's runner with Research's failure rules:

* a model-side failure (timeout, provider error, invalid output, a local validator saying no) NEVER raises:
  the deterministic fallback runs, the result says ``used_fallback`` and the run counts one failure and shows
  a ``warning`` event;
* a budget stop, a missing replay cassette, a forbidden live call and a cancellation are NOT model failures and
  must not hide behind a fallback: they propagate so the run can stop (``budget_stop``), fail loudly, or end;
* a zero-token answer from a billable (live) call counts as a failure (the Compass lesson: timeouts with
  0 tokens went uncounted), while a mock answer, which always reports zero cost, does not.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from mde_contracts.gateway import CallContext, CallSpec, GatewayClient
from mde_core.cancel import CancellationToken, NeverCancelled
from mde_core.clock import Clock, SystemClock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CassetteMissing,
    CircuitOpen,
    GatewayError,
    LiveCallForbidden,
)
from mde_core.logging import get_logger
from mde_research_agent.config import RUN_SCOPE_KIND
from mde_research_agent.ids import Ids, UlidIds

log = get_logger(__name__)

I = TypeVar("I", bound=BaseModel)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741

# Never swallowed by a fallback.
PROPAGATE: tuple[type[BaseException], ...] = (
    BudgetExceeded,
    CassetteMissing,
    LiveCallForbidden,
    CallCancelled,
)
FALLBACK_ON: tuple[type[BaseException], ...] = (GatewayError, TimeoutError, asyncio.TimeoutError)
Validator = Callable[[Any, Any], list[str]]
"""``validator(input, output) -> errors``; an empty list means the output is acceptable."""


@dataclass(frozen=True)
class ResearchCallSpec(Generic[I, O]):
    """A Research call: the gateway spec, a timeout, local validators and the deterministic fallback."""

    spec: CallSpec[I, O]
    timeout_s: float
    fallback: Callable[[I], O]
    validators: tuple[Validator, ...] = ()
    checker: bool = False  # the checker route (RSCH-4): a different model when one is configured

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass
class RunResult(Generic[O]):
    output: O
    used_fallback: bool = False
    failed: bool = (
        False  # counts into ``budget.failures`` (fallback used, or a zero-token live answer)
    )
    reason: str = ""
    repaired: bool = False
    cache_hit: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    live: bool = False
    model: str = ""
    errors: list[str] = field(default_factory=list)


CIRCUIT_WAITS = 3  # how many times a call waits out an open circuit before it uses its fallback
CIRCUIT_WAIT_CAP_S = 45.0  # the longest single wait


class CallRunner:
    """Runs :class:`ResearchCallSpec` objects through a ``GatewayClient`` inside one run's budget scope."""

    def __init__(
        self,
        gateway: GatewayClient,
        *,
        run_id: str,
        tenant_id: str = "research",
        clock: Clock | None = None,
        ids: Ids | None = None,
        cancel: CancellationToken | None = None,
        extra_scopes: tuple[str, ...] = (),
    ) -> None:
        self._extra_scopes = extra_scopes
        self._gateway = gateway
        self._run_id = run_id
        self._tenant = tenant_id
        self._clock = clock or SystemClock()
        self._ids = ids or UlidIds()
        self._cancel = cancel or NeverCancelled()
        self.calls: list[tuple[str, RunResult[Any]]] = []

    def for_tenant(self, tenant_id: str) -> CallRunner:
        """A runner on the same gateway, scope and call list under another tenant id. The gateway's cache is keyed by
        tenant, so an experiment that repeats a question for independent answers gives each repeat its own."""
        other = copy.copy(self)
        other._tenant = tenant_id
        return other

    def context(self, cs: ResearchCallSpec[Any, Any]) -> CallContext:
        return CallContext(
            request_id=self._ids.new("call"),
            budget_scope=f"{RUN_SCOPE_KIND}:{self._run_id}",
            extra_scopes=self._extra_scopes,
            purpose=f"research.{cs.id}",
            session_id=self._run_id,
            tenant_id=self._tenant,
            cancel=self._cancel,
        )

    async def run(self, cs: ResearchCallSpec[I, O], inp: I) -> RunResult[O]:
        started = time.monotonic()
        self._cancel.check()
        ctx = self.context(cs)
        waits = 0
        while True:
            try:
                result = await asyncio.wait_for(
                    self._gateway.run(cs.spec, inp, ctx), timeout=cs.timeout_s + 5.0
                )
                break
            except PROPAGATE:
                raise
            except CircuitOpen as exc:
                # The gateway stopped calling a provider that failed repeatedly, for a few seconds. A deterministic
                # fallback cannot extract facts, so a run that skipped every call during that window ended with no
                # results after one provider hiccup (2026-11-13, Medium run, 134 refused calls in a minute): wait out the
                # window, once the breaker lets a probe through the work resumes.
                retry_in = float(
                    (getattr(exc, "details", None) or {}).get("retry_in_seconds", 0) or 0
                )
                if waits >= CIRCUIT_WAITS or retry_in > CIRCUIT_WAIT_CAP_S:
                    return self._fallback(cs, inp, started, type(exc).__name__)
                waits += 1
                await asyncio.sleep(max(0.5, retry_in + 0.5))
                self._cancel.check()
            except FALLBACK_ON as exc:
                return self._fallback(cs, inp, started, type(exc).__name__)
        output = result.output
        if output is None:
            return self._fallback(cs, inp, started, "empty_output")
        errors: list[str] = []
        for validator in cs.validators:
            errors.extend(validator(inp, output))
        if errors:
            return self._fallback(cs, inp, started, "; ".join(errors)[:300])
        usage = result.usage
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        live = result.mode == "live"
        res = RunResult(
            output=output,
            failed=live and tokens_in == 0 and tokens_out == 0,
            reason="zero_token" if live and tokens_in == 0 and tokens_out == 0 else "",
            repaired=bool(result.repaired),
            cache_hit=bool(result.cache_hit),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_micros=int(getattr(usage, "cost_micros", 0) or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
            live=live,
            model=str(getattr(result, "model", "") or ""),
        )
        self.calls.append((cs.id, res))
        return res

    def _fallback(
        self, cs: ResearchCallSpec[I, O], inp: I, started: float, reason: str
    ) -> RunResult[O]:
        log.warning("research_call_fallback", call=cs.id, reason=reason)
        res = RunResult(
            output=cs.fallback(inp),
            used_fallback=True,
            failed=True,
            reason=reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            errors=[reason],
        )
        self.calls.append((cs.id, res))
        return res
