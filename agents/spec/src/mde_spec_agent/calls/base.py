"""One place that runs a Spec model call (doc 18 4, doc 19 5).

* Every call goes through `SpendGuard` (reserve the worst case, reconcile, fail closed): there is no other path to a
  model, so the USD cap cannot be bypassed (a test uses a scripted gateway that tries to overspend).
* A model-side failure (timeout, provider error, invalid output, a local validator saying no) uses ONE re-ask per NODE in
  the whole run (batched per call kind by the caller), then the deterministic fallback with `flagged=True`.
* A budget refusal, a stop request, a missing cassette, a forbidden live call and a cancellation are NOT model failures
  and are never hidden behind a fallback: they propagate so the step checkpoints and the run ends `partial`.
* Truncation is reported as `Truncated` so the caller splits the batch (a fused call splits back into its parts).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from mde_contracts.gateway import CallContext, CallSpec, GatewayClient
from mde_core.cancel import CancellationToken, NeverCancelled
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CassetteMissing,
    CircuitOpen,
    GatewayError,
    LiveCallForbidden,
)
from mde_core.ids import new_id
from mde_spec_agent.config import RUN_SCOPE_KIND
from mde_spec_agent.run.concurrency import RATE_LIMIT_STATUS, Limiter, watching
from mde_spec_agent.spend import SpendGuard, SpendRefused, StopRequested
from mde_spec_core.callkit import ReaskLedger
from mde_spec_core.canon import canonical_json
from mde_spec_core.render import TokenCounter

I = TypeVar("I", bound="CallInput")  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741

PROPAGATE: tuple[type[BaseException], ...] = (
    BudgetExceeded,
    CassetteMissing,
    CircuitOpen,  # a rate-limit or credit storm opens the breaker: the run ends partial and resumable, not degraded
    LiveCallForbidden,
    CallCancelled,
    SpendRefused,
    StopRequested,
)
Validator = Callable[[Any, Any], list[str]]
TEMPLATE_SLACK_TOKENS = 2500  # rules, vocabularies and schema text around the input


class NoCredit(CircuitOpen):
    """The provider refused for money (402): nothing more can run until a top-up. The run ends partial and resumable."""


def stop_on_provider_refusal(exc: BaseException) -> None:
    """A provider that is out of credit or still rate-limiting after the gateway's own retries is a STOP for the run,
    not a node to degrade: a fallback written now would be replayed from the checkpoint after the top-up."""
    status = getattr(exc, "status", None)
    if status == 402:
        raise NoCredit(f"provider refused for credit: {str(exc)[:120]}") from exc
    if status == 429:
        raise CircuitOpen(f"provider is rate limiting: {str(exc)[:120]}") from exc


class Truncated(RuntimeError):
    """The model's output hit its limit: the caller redoes the unfinished items in smaller batches."""


def is_truncation(exc: BaseException) -> bool:
    """Did this error come from an answer that was cut off at the output limit?

    The gateway says so with a typed flag (`details["truncated"]` on its `StructuredOutputError`, set after its own
    escalated retry also ran out), which crosses the HTTP flavour too. A provider adapter or a test double that can only
    raise a plain error may say "truncated" in its message; that older signal is still honoured, but it is the second
    check, not the first."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details.get("truncated") is True:
        return True
    return "truncat" in str(exc).lower()


class CallInput(BaseModel):
    """Base of every call input. `reask` carries the validator messages of ONE targeted re-ask, word for word."""

    model_config = ConfigDict(extra="forbid")
    reask: list[str] = []


@dataclass(frozen=True)
class SpecCallSpec(Generic[I, O]):
    spec: CallSpec[I, O]
    kind: str  # the call kind ("PROD-MODULE"): re-asks are batched per kind
    timeout_s: float
    fallback: Callable[[I], O]
    validators: tuple[Validator, ...] = ()

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass
class RunResult(Generic[O]):
    output: O
    used_fallback: bool = False
    flagged: bool = (
        False  # the fallback or a failed re-ask: shown at G2 as degraded, never an unmarked hole
    )
    reasked: bool = False
    repaired: bool = False
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    first_errors: list[str] = field(
        default_factory=list
    )  # what the FIRST answer got wrong (when it was re-asked)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    live: bool = False
    model: str = ""


class CallRunner:
    def __init__(
        self,
        gateway: GatewayClient,
        guard: SpendGuard,
        *,
        run_id: str,
        tenant_id: str = "spec",
        ledger: ReaskLedger | None = None,
        cancel: CancellationToken | None = None,
        on_call: Callable[[str, str, RunResult[Any]], None] | None = None,
        extra_scopes: tuple[str, ...] = (),
        counter: TokenCounter | None = None,
        limiter: Limiter | None = None,
    ) -> None:
        self._gateway = gateway
        self._limiter = limiter
        self._guard = guard
        self._run_id = run_id
        self._tenant = tenant_id
        self.ledger = ledger or ReaskLedger()
        self._cancel = cancel or NeverCancelled()
        self._on_call = on_call
        self._extra = extra_scopes
        self._counter = counter or TokenCounter(3.0)
        self.calls: list[tuple[str, RunResult[Any]]] = []

    def context(self, cs: SpecCallSpec[Any, Any]) -> CallContext:
        return CallContext(
            request_id=new_id("call"),
            budget_scope=f"{RUN_SCOPE_KIND}:{self._run_id}",
            extra_scopes=self._extra,
            purpose=f"spec.{cs.id}",
            session_id=self._run_id,
            tenant_id=self._tenant,
            cancel=self._cancel,
        )

    def estimate_input_tokens(self, inp: BaseModel) -> int:
        return (
            self._counter.count(canonical_json(inp.model_dump(mode="json"))) + TEMPLATE_SLACK_TOKENS
        )

    async def _once(self, cs: SpecCallSpec[I, O], inp: I) -> Any:
        ctx = self.context(cs)
        limiter = self._limiter
        epoch = limiter.epoch if limiter is not None else 0

        async def go() -> Any:
            with watching(
                limiter
            ):  # every 429 the gateway sees for this call lowers the width (once per event)
                return await asyncio.wait_for(
                    self._gateway.run(cs.spec, inp, ctx), timeout=cs.timeout_s + 5.0
                )

        try:
            result = await self._guard.call(
                go,
                cost_of=lambda r: int(getattr(getattr(r, "usage", None), "cost_micros", 0) or 0),
                input_tokens=self.estimate_input_tokens(inp),
                max_output_tokens=int(cs.spec.max_output_tokens),
                label=cs.id,
                run_id=self._run_id,
            )
        except BaseException as exc:
            if limiter is not None and getattr(exc, "status", None) == RATE_LIMIT_STATUS:
                # a 429 that survived the retries: the in-process gateway already told the watch, a remote one
                # could not; the epoch makes the second report a no-op
                limiter.note_rate_limit(since=epoch)
            raise
        if limiter is not None:
            await limiter.call_ok()
        return result

    async def run(
        self, cs: SpecCallSpec[I, O], inp: I, *, node_ids: Sequence[str] = (), step: str = ""
    ) -> RunResult[O]:
        started = time.monotonic()
        self._cancel.check()
        errors: list[str] = []
        result: Any = None
        spent = {"cost": 0, "in": 0, "out": 0}

        def take(r: Any) -> None:
            """Every attempt that reached the model costs money, whether or not its answer was used."""
            u = getattr(r, "usage", None)
            spent["cost"] += int(getattr(u, "cost_micros", 0) or 0)
            spent["in"] += int(getattr(u, "input_tokens", 0) or 0)
            spent["out"] += int(getattr(u, "output_tokens", 0) or 0)

        try:
            result = await self._once(cs, inp)
            take(result)
        except PROPAGATE:
            raise
        except (GatewayError, TimeoutError) as exc:
            stop_on_provider_refusal(exc)
            if is_truncation(exc):
                raise Truncated(str(exc)) from exc
            errors = [f"{type(exc).__name__}: {str(exc)[:160]}"]
        out = getattr(result, "output", None) if result is not None else None
        if out is not None:
            for v in cs.validators:
                errors.extend(v(inp, out))
        first_errors = list(errors)
        reasked = False
        if errors and node_ids and not any(self.ledger.has_used(n) for n in node_ids):
            reasked = True
            for n in node_ids:
                self.ledger.mark(n)
            try:
                result2 = await self._once(cs, inp.model_copy(update={"reask": errors[:20]}))
                take(result2)
                out2 = getattr(result2, "output", None)
                errs2: list[str] = []
                if out2 is not None:
                    for v in cs.validators:
                        errs2.extend(v(inp, out2))
                if out2 is not None and not errs2:
                    result, out, errors = result2, out2, []
            except PROPAGATE:
                raise
            except (GatewayError, TimeoutError) as exc2:
                stop_on_provider_refusal(exc2)
        live = getattr(result, "mode", "") == "live"
        if out is None or errors:
            res: RunResult[O] = RunResult(
                output=cs.fallback(inp),
                used_fallback=True,
                flagged=True,
                reasked=reasked,
                reason="; ".join(errors)[:300] or "empty_output",
                errors=errors,
                first_errors=first_errors,
                tokens_in=spent["in"],
                tokens_out=spent["out"],
                cost_micros=spent["cost"],
                latency_ms=int((time.monotonic() - started) * 1000),
                live=live,
            )
        else:
            res = RunResult(
                output=out,
                reasked=reasked,
                repaired=bool(getattr(result, "repaired", False)),
                first_errors=first_errors,
                tokens_in=spent["in"],
                tokens_out=spent["out"],
                cost_micros=spent["cost"],
                latency_ms=int((time.monotonic() - started) * 1000),
                live=live,
                model="",
            )
        self.calls.append((cs.id, res))
        if self._on_call:
            self._on_call(step, cs.kind, res)
        return res
