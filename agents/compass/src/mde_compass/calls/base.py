"""The call framework: one place that runs a structured call, validates, falls back and logs.

Pipeline for one call (the gateway does the heavy lifting; ADR 0014):
  validate input -> ``gateway.run`` (render with untrusted data wrapped, reserve budget, cache, provider with
  timeout, validate output, one repair at low effort, retries on 429/5xx/timeouts) -> local validators ->
  deterministic fallback on any failure -> record cost -> log.

Specs live in the call's own folder (``calls/<name>/spec.py``) and are plain ``CompassCallSpec`` objects: the
gateway ``CallSpec`` plus what it has no fields for (lane, timeout, local validators, the fallback function).
No registration is needed to call them (``gateway.run`` takes the spec object); ``StaticRegistry`` serves them
for catalog and HTTP lookup.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from mde_compass.config import CALL_PROFILES, Lane, Route
from mde_compass.store.ports import CallLogEntry
from mde_contracts.gateway import CallContext, CallSpec, GatewayClient
from mde_core.clock import Clock, SystemClock
from mde_core.errors import GatewayError
from mde_core.ids import new_id
from mde_core.logging import get_logger

log = get_logger(__name__)

I = TypeVar("I", bound=BaseModel)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741

FALLBACK_ON: tuple[type[BaseException], ...] = (GatewayError, TimeoutError, asyncio.TimeoutError)
Validator = Callable[[Any, Any], list[str]]
"""``validator(input, output) -> list of error strings``; an empty list means the output is acceptable."""


@dataclass(frozen=True)
class CompassCallSpec(Generic[I, O]):
    """A Compass call: the gateway spec plus lane, timeout, local validators and the deterministic fallback.

    ``fallback`` is a pure function ``input -> output`` (hard rule 4). The mock handler is usually the same
    function or a thin wrapper, so the whole agent runs offline.
    """

    spec: CallSpec[I, O]
    lane: Lane
    timeout_s: float
    fallback: Callable[[I], O]
    validators: tuple[Validator, ...] = ()

    @property
    def id(self) -> str:
        return self.spec.id


def profile_for(call_id: str) -> tuple[Lane, Route, float]:
    p = CALL_PROFILES[call_id]
    return p.lane, p.route, p.timeout_s


@dataclass
class RunResult(Generic[O]):
    output: O
    used_fallback: bool = False
    reason: str = ""  # why the fallback was used (exception class or validator message)
    repaired: bool = False
    cache_hit: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    calls_made: int = 1
    errors: list[str] = field(default_factory=list)


LogSink = Callable[[str, CallLogEntry], Awaitable[None]]
"""``sink(session_id, entry)``: where call-log entries go (the engine writes them to the store)."""


class SpendGuard:
    """Protocol-ish base for the Compass-side budget (``mde_compass.budget.CompassBudget``)."""

    def check(self, session_id: str, *, estimate_micros: int = 0) -> None:  # pragma: no cover
        raise NotImplementedError

    def record(self, session_id: str, cost_micros: int) -> None:  # pragma: no cover
        raise NotImplementedError


class CallRunner:
    """Runs ``CompassCallSpec`` objects through a ``GatewayClient``; never raises for a model-side failure."""

    def __init__(
        self,
        gateway: GatewayClient,
        *,
        clock: Clock | None = None,
        guard: SpendGuard | None = None,
        sink: LogSink | None = None,
        extra_scopes: tuple[str, ...] = (),
    ) -> None:
        self._gateway = gateway
        self._clock = clock or SystemClock()
        self._guard = guard
        self._sink = sink
        self._extra_scopes = extra_scopes

    def context(
        self, cs: CompassCallSpec[Any, Any], *, session_id: str, tenant_id: str
    ) -> CallContext:
        deadline = self._clock.now() + timedelta(seconds=cs.timeout_s)
        return CallContext(
            request_id=new_id("call"),
            budget_scope=f"session:{session_id}",
            purpose=f"compass.{cs.id}",
            session_id=session_id,
            tenant_id=tenant_id,
            extra_scopes=self._extra_scopes,
            deadline=deadline,
        )

    async def run(
        self, cs: CompassCallSpec[I, O], inp: I, *, session_id: str, tenant_id: str
    ) -> RunResult[O]:
        started = time.monotonic()
        ctx = self.context(cs, session_id=session_id, tenant_id=tenant_id)
        try:
            if self._guard is not None:
                self._guard.check(session_id)
            result = await self._gateway.run(cs.spec, inp, ctx)
        except FALLBACK_ON as exc:
            return await self._fallback(cs, inp, session_id, ctx, started, type(exc).__name__)
        output = result.output
        if output is None:
            return await self._fallback(cs, inp, session_id, ctx, started, "empty_output")
        errors: list[str] = []
        for validator in cs.validators:
            errors.extend(validator(inp, output))
        if errors:
            return await self._fallback(cs, inp, session_id, ctx, started, "; ".join(errors)[:300])
        usage = result.usage
        cost = int(getattr(usage, "cost_micros", 0) or 0)
        res = RunResult(
            output=output,
            repaired=bool(result.repaired),
            cache_hit=bool(result.cache_hit),
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            cost_micros=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        if self._guard is not None:
            self._guard.record(session_id, cost)
        await self._log(
            cs,
            ctx,
            session_id,
            res,
            "cache_hit" if res.cache_hit else ("repaired" if res.repaired else "ok"),
            "",
        )
        return res

    async def _fallback(
        self,
        cs: CompassCallSpec[I, O],
        inp: I,
        session_id: str,
        ctx: CallContext,
        started: float,
        reason: str,
    ) -> RunResult[O]:
        log.warning("compass_call_fallback", call=cs.id, reason=reason)
        res = RunResult(
            output=cs.fallback(inp),
            used_fallback=True,
            reason=reason,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        await self._log(cs, ctx, session_id, res, "fallback", reason)
        return res

    async def _log(
        self,
        cs: CompassCallSpec[Any, Any],
        ctx: CallContext,
        session_id: str,
        res: RunResult[Any],
        outcome: str,
        reason: str,
    ) -> None:
        if self._sink is None:
            return
        await self._sink(
            session_id,
            CallLogEntry(
                call_id=ctx.request_id,
                spec_id=cs.id,
                spec_version=str(cs.spec.version),
                tokens_in=res.tokens_in,
                tokens_out=res.tokens_out,
                cost_micros=res.cost_micros,
                latency_ms=res.latency_ms,
                outcome=outcome,  # type: ignore[arg-type]
                reason=reason[:300],
                at=self._clock.now(),
            ),
        )
