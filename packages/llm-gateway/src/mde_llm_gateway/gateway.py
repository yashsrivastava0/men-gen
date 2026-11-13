"""The gateway pipeline (brief 7.3). One ``Gateway.run`` is one logical call:

    1. validate the input, hard-check the spec (untrusted + tools is an error), price the model
    2. render the templates (untrusted fields wrapped), build the strict schema
    3. cache lookup (a hit costs nothing and never touches the budget)
    4. per provider attempt: route slot -> circuit check -> RESERVE worst case -> provider call ->
       COMMIT the real cost (or the partial / estimated cost on failure, then release the rest)
    5. retries on 429 / 5xx / timeouts / connection errors only (backoff + jitter, Retry-After)
    6. parse and validate locally; exactly ONE repair call; still invalid -> StructuredOutputError
    7. ``incomplete`` because of max_output_tokens (reasoning counts against it) -> ONE retry with a
       cap bounded by the spec ceiling (``escalate_cap``); it reserves again, so the budget guard
       can refuse it; if that also runs out and a repair at effort "low" could not give the model
       more room, the call ends in StructuredOutputError (the engine falls back)
    8. write the ``LlmCallRecord`` to the optional sink and a structured log line

Every provider attempt (including retries, repair and the escalated retry) has its own reservation,
so all loops are metered. Budget refusals are never retried.

Settlement is fail-closed (a lost cost is the one bug this module must never have):

* a commit that fails for ANY reason (ledger write error, lock timeout, unpriceable or negative
  usage) books the reservation's WORST CASE instead; if even that cannot be written the error stops
  the call, the reservation stays open, and the ledger counts a stale reservation as spent;
* a timeout, a dropped connection, a cancellation or a bug after the request left (the reservation
  is held) books the worst case, not just the input tokens: nothing is known about the output or a
  web-search fee, so none is assumed absent;
* only a failure the provider proves was not billed (no ``maybe_billed``, no usage) releases.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import cast

from pydantic import BaseModel, ValidationError

from mde_contracts.gateway import (
    BudgetStatus,
    CallContext,
    CallResult,
    CallSpec,
    CallStatus,
    Effort,
    I,
    LlmCallRecord,
    LlmCallSink,
    O,
    ToolSpec,
    Usage,
)
from mde_core.clock import Clock, SystemClock
from mde_core.errors import ConfigError, MdeError, ProviderError, StructuredOutputError
from mde_core.hashing import canonical_json, hash_json
from mde_core.ids import new_id
from mde_core.logging import get_logger, register_secrets
from mde_core.redact import redact_text, redact_value
from mde_core.settings import Settings
from mde_llm_gateway.budget import BudgetGuard, Reservation, usage_is_invalid
from mde_llm_gateway.circuit import CircuitBreaker, breaker_key
from mde_llm_gateway.errors import DeadlineExceeded
from mde_llm_gateway.limits import RouteLimits
from mde_llm_gateway.providers.base import (
    CallKind,
    Provider,
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
)
from mde_llm_gateway.registry import AnySpec, SpecRegistry
from mde_llm_gateway.render import PromptRenderer, RenderedPrompt, check_spec
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.strict_schema import schema_hash, strict_schema_name, to_strict_schema

MAX_ESCALATED_OUTPUT_TOKENS = 32_768
# The API treats a tool's ``max_tool_calls`` as best effort: live batch L2 saw one search billed two
# tool calls under a cap of 1 (USD 0.0206 against a USD 0.0120 reservation, a logged
# ``budget_reservation_overrun``; D-082). The guard therefore reserves for twice the cap, so the
# held worst case is not below what the provider can bill; the real cost is committed afterwards.
TOOL_CALL_RESERVE_FACTOR = 2
MAX_LOGGED_PROMPT_CHARS = 4_000
_PREVIOUS_OUTPUT_CHARS = 8_000
# 402 is a spent balance: every following call would fail the same way, so it opens the breaker.
_STATUS_COUNTS_FOR_BREAKER = frozenset({401, 402, 403})


def escalate_cap(cap: int, ceiling: int = MAX_ESCALATED_OUTPUT_TOKENS) -> int:
    """Bounded higher ``max_output_tokens`` for the single retry of an ``incomplete`` response.

    At most twice the spec's cap, at most 8,192 tokens more, and never above ``ceiling``
    (``GatewayConfig.max_escalated_output_tokens``). A result that is not above ``cap`` means
    "no room to escalate".
    """
    return min(cap * 2, cap + 8_192, ceiling)


def _ran_out_of_tokens(response: ProviderResponse) -> bool:
    """The response was cut off because ``max_output_tokens`` (visible text AND reasoning) ran out."""
    return response.status == "incomplete" and response.incomplete_reason == "max_output_tokens"


# A repair call runs at effort "low": for a spec that already runs at "low" or "none" it cannot give
# the model more room than the attempts that just ran out of tokens, so it is not worth paying for.
_EFFORTS_A_REPAIR_CANNOT_LOWER = frozenset({"none", "low"})


@dataclass(frozen=True)
class GatewayConfig:
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    breaker_failures: int = 5
    breaker_open_seconds: float = 30.0
    concurrency: Mapping[str, int] | None = None  # route -> parallel calls (None: defaults)
    rpm: Mapping[str, int] | None = None  # route -> requests per minute (None: defaults)
    timeout_s: Mapping[str, float] = field(
        default_factory=lambda: {"fast": 30.0, "standard": 60.0, "deep": 120.0}
    )
    tool_timeout_multiplier: float = 2.0
    timeout_backstop_s: float = 5.0  # asyncio backstop added on top of the provider timeout
    max_untrusted_chars: int = 20_000
    cache_size: int = 256
    # Hard ceiling for the one escalated retry of a response that ran out of output tokens (a live
    # batch can lower it; it can never exceed the module's ``MAX_ESCALATED_OUTPUT_TOKENS``).
    max_escalated_output_tokens: int = MAX_ESCALATED_OUTPUT_TOKENS


@dataclass
class _Held:
    reservation: Reservation


@dataclass
class _CallState:
    call_id: str
    ctx: CallContext
    spec: AnySpec
    model: str
    usage: Usage = field(default_factory=Usage)
    prompt_hash: str | None = None
    prompt_text: str | None = None  # redacted; only set when LOG_PROMPTS=1
    cassette_key: str | None = None
    repaired: bool = False
    attempts: int = 0


def _failure_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class Gateway:
    """The core pipeline. Build one with :func:`mde_llm_gateway.factory.create_gateway`."""

    def __init__(
        self,
        *,
        registry: SpecRegistry,
        provider: Provider,
        guard: BudgetGuard,
        settings: Settings,
        sink: LlmCallSink | None = None,
        clock: Clock | None = None,
        config: GatewayConfig | None = None,
        rng: random.Random | None = None,
        ids: Callable[[str], str] = new_id,
    ) -> None:
        if provider.billable != guard.bills:
            raise ConfigError(
                "provider and budget guard disagree about billing: a billable provider needs the "
                "shared ledger and a non-billable one must use the in-memory ledger",
            )
        self.registry = registry
        self.provider = provider
        self.guard = guard
        self.settings = settings
        self.sink = sink
        self.clock: Clock = clock or SystemClock()
        self.config = config or GatewayConfig()
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self._ids = ids
        self._renderer = PromptRenderer(max_untrusted_chars=self.config.max_untrusted_chars)
        self._limits = RouteLimits(
            clock=self.clock, concurrency=self.config.concurrency, rpm=self.config.rpm
        )
        self._breakers: dict[str, CircuitBreaker] = {}
        self._schemas: dict[tuple[str, int], tuple[dict[str, object], str]] = {}
        self._cache: OrderedDict[str, CallResult[BaseModel]] = OrderedDict()
        self._log = get_logger("mde.llm_gateway")
        self._secrets = settings.secret_values()
        register_secrets(self._secrets)

    # ------------------------------------------------------------------ public
    def budget_status(self) -> BudgetStatus:
        return self.guard.status()

    def breaker(self, route: str, tool_kinds: Iterable[str] = ()) -> CircuitBreaker:
        """The breaker for ``route`` and the class of tools used (``()`` for plain structured
        calls, ``("web_search",)`` for search calls): the two never share state."""
        key = breaker_key(route, tool_kinds)
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(
                failure_threshold=self.config.breaker_failures,
                open_seconds=self.config.breaker_open_seconds,
                clock=self.clock,
            )
        return self._breakers[key]

    async def run(self, spec: CallSpec[I, O], input: I, ctx: CallContext) -> CallResult[O]:
        any_spec = cast("AnySpec", spec)
        started = self.clock.monotonic()
        model = self.settings.model_for_route(ctx.route_override or spec.route)
        state = _CallState(call_id=self._ids("call"), ctx=ctx, spec=any_spec, model=model)
        try:
            result = await self._run(any_spec, input, ctx, state, started)
        except BaseException as exc:
            await self._finish_failed(state, exc, started)
            raise
        await self._finish_ok(state, result)
        return cast("CallResult[O]", result)

    # ------------------------------------------------------------------ pipeline
    async def _run(
        self, spec: AnySpec, input: BaseModel, ctx: CallContext, state: _CallState, started: float
    ) -> CallResult[BaseModel]:
        ctx.cancel.check()
        check_spec(spec)
        validated = self._renderer.validate_input(spec, input)
        self._price_check(spec, state.model)
        rendered = self._renderer.render(spec, validated)
        state.prompt_hash = rendered.prompt_hash
        if self.settings.log_prompts:  # opt-in, dev only, always redacted
            state.prompt_text = self._safe(f"[system]\n{rendered.system}\n[user]\n{rendered.user}")[
                :MAX_LOGGED_PROMPT_CHARS
            ]
        schema, s_hash = self._schema_for(spec)

        cache_key: str | None = None
        if spec.cacheable:
            # The tenant is part of the key: one tenant must never get a cache hit (a signal, and a
            # shared result) from another tenant's identical input (security review 2, D-071).
            cache_key = hash_json(
                [ctx.tenant_id or "", spec.id, spec.version, state.model, rendered.system,
                 rendered.user, s_hash, spec.reasoning_effort]
            )  # fmt: skip
            hit = self._cache.get(cache_key)
            if hit is not None:
                self._cache.move_to_end(cache_key)
                return hit.model_copy(
                    update={
                        "call_id": state.call_id,
                        "request_id": ctx.request_id,
                        "status": "cache_hit",
                        "cache_hit": True,
                        "usage": Usage(),
                        "repaired": False,
                        "cassette_key": None,
                        "latency_ms": self._elapsed_ms(started),
                    }
                )

        tools = _effective_tools(spec, validated)

        def request(kind: CallKind, user: str, effort: Effort, max_out: int) -> ProviderRequest:
            return ProviderRequest(
                spec=spec,
                input=validated,
                model=state.model,
                system=rendered.system,
                user=user,
                effort=effort,
                max_output_tokens=max_out,
                tools=tools,
                schema=schema,
                schema_name=strict_schema_name(spec.id),
                schema_hash=s_hash,
                timeout_s=self._timeout_for(spec, ctx),
                kind=kind,
            )

        response = await self._call(
            request("main", rendered.user, spec.reasoning_effort, spec.max_output_tokens),
            ctx,
            state,
        )
        if _ran_out_of_tokens(response):
            # ONE retry, with a bounded higher cap. It is a provider attempt like any other: it
            # reserves its own (larger) worst case, so an exhausted budget stops it (BudgetExceeded).
            bigger = escalate_cap(spec.max_output_tokens, self._escalation_ceiling())
            if bigger > spec.max_output_tokens:
                response = await self._call(
                    request("escalate", rendered.user, spec.reasoning_effort, bigger), ctx, state
                )

        output, errors, repairable = self._validate(spec, response)
        if errors:
            if (
                _ran_out_of_tokens(response)
                and spec.reasoning_effort in _EFFORTS_A_REPAIR_CANNOT_LOWER
            ):
                repairable = False  # reasoning ate the cap twice: the engine falls back instead
            if not (spec.repair and spec.output_model is not None and repairable):
                raise self._structured_error(spec, errors, repaired=False)
            repair_user = _repair_prompt(rendered.user, response.text, errors)
            response = await self._call(
                request("repair", repair_user, "low", spec.max_output_tokens), ctx, state
            )
            state.repaired = True
            output, errors, _ = self._validate(spec, response)
            if errors:
                raise self._structured_error(spec, errors, repaired=True)

        status: CallStatus = "repaired" if state.repaired else "ok"
        result: CallResult[BaseModel] = CallResult(
            call_id=state.call_id,
            spec_id=spec.id,
            spec_version=spec.version,
            model=state.model,
            mode=self.provider.mode,
            status=status,
            output=output,
            text=response.text if spec.output_model is None else None,
            citations=list(response.citations),
            usage=state.usage,
            repaired=state.repaired,
            cache_hit=False,
            latency_ms=self._elapsed_ms(started),
            request_id=ctx.request_id,
            cassette_key=state.cassette_key,
            search_calls=response.search_calls,
        )
        if cache_key is not None:
            self._cache[cache_key] = result
            while len(self._cache) > self.config.cache_size:
                self._cache.popitem(last=False)
        return result

    # ------------------------------------------------------------------ provider attempts
    def _escalation_ceiling(self) -> int:
        return min(self.config.max_escalated_output_tokens, MAX_ESCALATED_OUTPUT_TOKENS)

    def _output_ceiling(self, spec: AnySpec) -> int:
        """The most ``max_output_tokens`` any request of a call to ``spec`` may carry."""
        cap = spec.max_output_tokens
        return max(cap, escalate_cap(cap, self._escalation_ceiling()))

    async def _call(
        self, req: ProviderRequest, ctx: CallContext, state: _CallState
    ) -> ProviderResponse:
        ceiling = self._output_ceiling(req.spec)
        if req.max_output_tokens > ceiling:  # defence in depth: nothing above the ceiling is sent
            raise ConfigError(
                f"{req.spec_id}: max_output_tokens {req.max_output_tokens} is above the ceiling "
                f"{ceiling} for this spec; the request was not sent"
            )
        policy = self.config.retry
        route = ctx.route_override or req.spec.route
        began = self.clock.monotonic()
        failed = 0
        while True:
            ctx.cancel.check()
            self._check_deadline(ctx)
            breaker = self.breaker(route, (t.kind for t in req.tools))
            breaker.before_call()  # CircuitOpen: raised before any budget is reserved
            state.attempts += 1
            held: _Held | None = None
            try:
                async with self._limits.slot(route):
                    ctx.cancel.check()
                    held = self._reserve(req, ctx, state)
                    response = await self._provider_call(replace(req, attempt=failed + 1))
            except ProviderError as exc:
                usage = self._settle_failure(held, exc)
                state.usage = state.usage + usage
                if self._counts_for_breaker(exc):
                    breaker.record_failure()
                else:
                    breaker.record_neutral()
                failed += 1
                if not exc.retryable or failed >= policy.max_attempts:
                    raise self._scrub_error(exc) from None
                delay = policy.delay(failed, retry_after=exc.retry_after, rng=self._rng)
                spent = self.clock.monotonic() - began
                if spent + delay > policy.max_total_seconds or self._deadline_before(ctx, delay):
                    raise self._scrub_error(exc) from None
                self._log.info(
                    "llm_retry",
                    request_id=ctx.request_id,
                    call_id=state.call_id,
                    spec_id=req.spec_id,
                    attempt=failed,
                    delay_s=round(delay, 3),
                    status=exc.status,
                    error=self._safe(str(exc)),
                )
                await self.clock.sleep(delay)
                continue
            except BaseException:
                # Cancellation, a budget refusal inside the slot, a bug: tell nobody about provider
                # health. If a reservation is held the request may already be in flight or done.
                if held is not None:
                    try:
                        state.usage = state.usage + self._settle_unexpected(held)
                    except Exception as settle_exc:  # never mask a cancellation with a ledger error
                        self._log.critical(
                            "budget_settle_unexpected_failed",
                            reservation_id=held.reservation.reservation_id,
                            worst_case_micros=held.reservation.worst_case_micros,
                            error_type=type(settle_exc).__name__,
                        )
                breaker.record_neutral()
                raise
            breaker.record_success()
            state.usage = state.usage + self._settle_success(held, response, req)
            if response.cassette_key:
                state.cassette_key = response.cassette_key
            return response

    async def _provider_call(self, req: ProviderRequest) -> ProviderResponse:
        try:
            async with asyncio.timeout(req.timeout_s + self.config.timeout_backstop_s):
                return await self.provider.complete(req)
        except TimeoutError:
            raise ProviderCallError(
                f"provider call timed out after {req.timeout_s:.0f}s",
                retryable=True,
                maybe_billed=True,
            ) from None

    def _reserve(self, req: ProviderRequest, ctx: CallContext, state: _CallState) -> _Held:
        chars = len(req.system) + len(req.user)
        if req.schema is not None:
            chars += len(canonical_json(req.schema))
        tool_calls = sum(t.max_tool_calls for t in req.tools) * TOOL_CALL_RESERVE_FACTOR
        worst = self.guard.estimate_worst_case(
            req.model, prompt_chars=chars, max_output_tokens=req.max_output_tokens,
            max_tool_calls=tool_calls,
        )  # fmt: skip
        reservation = self.guard.reserve(
            ctx.scopes,
            worst,
            purpose=ctx.purpose,
            call_id=state.call_id,
            spec_id=req.spec_id,
            model=req.model,
            mode=self.provider.mode,
        )
        return _Held(reservation=reservation)

    def _book_worst_case(self, held: _Held, *, reason: str) -> Usage:
        """Close the reservation at its worst case: the safe answer whenever the real cost is unknown
        or cannot be recorded. A non-billing guard records 0. If this write fails too the error
        propagates (fail closed); the open reservation then counts as spent once it is stale."""
        reservation = held.reservation
        emit = self._log.critical if reason == "commit_failed" else self._log.warning
        emit(
            "llm_booked_worst_case",
            reservation_id=reservation.reservation_id,
            reason=reason,
            booked_micros=reservation.worst_case_micros,
            spec_id=reservation.spec_id,
        )
        return self.guard.commit_cost(reservation, reservation.worst_case_micros)

    def _settle_success(
        self, held: _Held | None, response: ProviderResponse, req: ProviderRequest
    ) -> Usage:
        assert held is not None
        reservation = held.reservation
        if response.usage is None:
            # The API reported nothing: never assume it was free. Book the reserved worst case.
            self._log.warning(
                "llm_usage_missing",
                spec_id=req.spec_id,
                worst_case_micros=reservation.worst_case_micros,
            )
            return self._book_worst_case(held, reason="usage_missing")
        usage = response.usage.model_copy(
            update={"tool_calls": max(response.usage.tool_calls, response.search_calls)}
        )
        try:
            recorded = self.guard.commit(reservation, usage)
        except Exception as exc:  # ledger write failure, lock timeout, ...
            self._log.critical(
                "llm_commit_failed",
                reservation_id=reservation.reservation_id,
                spec_id=req.spec_id,
                error_type=type(exc).__name__,
            )
            return self._book_worst_case(held, reason="commit_failed")
        if self.guard.bills and usage.tool_calls == 0 and not usage_is_invalid(usage):
            self.guard.estimator.observe(len(req.system) + len(req.user), usage.input_tokens)
        return recorded

    def _settle_failure(self, held: _Held | None, exc: ProviderError) -> Usage:
        if held is None:  # the failure came from before a reservation existed
            return Usage()
        reported = exc.usage if isinstance(exc, ProviderCallError) else None
        if reported is not None:
            try:
                return self.guard.commit(held.reservation, reported)
            except Exception as commit_exc:
                self._log.critical(
                    "llm_commit_failed",
                    reservation_id=held.reservation.reservation_id,
                    error_type=type(commit_exc).__name__,
                )
                return self._book_worst_case(held, reason="commit_failed")
        if isinstance(exc, ProviderCallError) and exc.maybe_billed and self.guard.bills:
            # A timeout or dropped connection: the request left and may have run to the end.
            return self._book_worst_case(held, reason="maybe_billed")
        self.guard.release(held.reservation)
        return Usage()

    def _settle_unexpected(self, held: _Held) -> Usage:
        """Cancellation or a bug while a reservation was held. A real call may already be in flight
        (or finished and mis-parsed), so a billing guard books the WORST CASE instead of releasing
        for free; non-billing guards just close the reservation."""
        if self.guard.bills:
            return self._book_worst_case(held, reason="unexpected_failure")
        self.guard.release(held.reservation)
        return Usage()

    # ------------------------------------------------------------------ validation / repair
    def _validate(
        self, spec: AnySpec, response: ProviderResponse
    ) -> tuple[BaseModel | None, list[str], bool]:
        """Returns ``(output, errors, repairable)``; ``errors`` empty means valid."""
        if response.refusal:
            return None, [f"the model refused: {self._safe(response.refusal)[:200]}"], False
        if response.status == "incomplete":
            reason = response.incomplete_reason or "unknown"
            return (
                None,
                [f"the response was cut off ({reason}) before it was complete"],
                reason == "max_output_tokens",
            )
        if spec.output_model is None:
            if not response.text.strip():
                return None, ["the web search answer was empty"], False
            return None, [], False
        try:
            data = json.loads(response.text)
        except ValueError as exc:
            return None, [f"the output is not valid JSON: {exc}"], True
        try:
            return spec.output_model.model_validate(data), [], True
        except ValidationError as exc:
            return None, _error_lines(exc), True

    def _structured_error(
        self, spec: AnySpec, errors: list[str], *, repaired: bool
    ) -> StructuredOutputError:
        return StructuredOutputError(
            f"{spec.id}: output still invalid after {'the repair attempt' if repaired else 'validation'}"
            f" ({len(errors)} problem(s)); the engine should use its fallback",
            details={"errors": errors[:10], "repaired": repaired, "call_id": spec.id},
        )

    # ------------------------------------------------------------------ helpers
    def _schema_for(self, spec: AnySpec) -> tuple[dict[str, object] | None, str]:
        if spec.output_model is None:
            return None, ""
        key = (spec.id, spec.version)
        cached = self._schemas.get(key)
        if cached is None:
            schema = to_strict_schema(spec.output_model)
            cached = (schema, schema_hash(schema))
            self._schemas[key] = cached
        return cached

    def _price_check(self, spec: AnySpec, model: str) -> None:
        self.guard.pricing.price_for(model)
        if spec.tools:
            self.guard.pricing.tool_price("web_search")

    def _timeout_for(self, spec: AnySpec, ctx: CallContext) -> float:
        route = ctx.route_override or spec.route
        timeout = self.config.timeout_s.get(route, 60.0)
        if spec.tools:
            timeout *= self.config.tool_timeout_multiplier
        if ctx.deadline is not None:
            remaining = (ctx.deadline - self.clock.now()).total_seconds()
            timeout = min(timeout, max(remaining, 0.001))
        return timeout

    def _check_deadline(self, ctx: CallContext) -> None:
        if ctx.deadline is not None and self.clock.now() >= ctx.deadline:
            raise DeadlineExceeded("the call deadline passed before the call could run")

    def _deadline_before(self, ctx: CallContext, delay: float) -> bool:
        if ctx.deadline is None:
            return False
        return (ctx.deadline - self.clock.now()).total_seconds() <= delay

    def _counts_for_breaker(self, exc: ProviderError) -> bool:
        return exc.retryable or exc.status in _STATUS_COUNTS_FOR_BREAKER

    def _elapsed_ms(self, started: float) -> int:
        return int((self.clock.monotonic() - started) * 1000)

    def _safe(self, text: str) -> str:
        """Scrub a string before it is logged, stored or raised: known keys, then key-like text."""
        out = text
        for secret in self._secrets:
            out = redact_value(out, secret)
        return redact_text(out)[0]

    def _scrub_error(self, exc: ProviderError) -> ProviderError:
        """The same error with its message scrubbed of any key-like text."""
        safe = self._safe(str(exc))
        if safe == str(exc):
            return exc
        if isinstance(exc, ProviderCallError):
            clean: ProviderError = ProviderCallError(
                safe, status=exc.status, retryable=exc.retryable, retry_after=exc.retry_after,
                usage=exc.usage, maybe_billed=exc.maybe_billed,
            )  # fmt: skip
        else:
            clean = ProviderError(
                safe, status=exc.status, retryable=exc.retryable, retry_after=exc.retry_after
            )
        return clean

    # ------------------------------------------------------------------ finish: record + log
    async def _finish_ok(self, state: _CallState, result: CallResult[BaseModel]) -> None:
        await self._record(state, result.status, error_code=None, latency_ms=result.latency_ms)
        self._log_call(state, status=result.status, latency_ms=result.latency_ms, error=None,
                       cache_hit=result.cache_hit)  # fmt: skip

    async def _finish_failed(self, state: _CallState, exc: BaseException, started: float) -> None:
        latency = self._elapsed_ms(started)
        code = exc.code if isinstance(exc, MdeError) else type(exc).__name__
        await self._record(state, "failed", error_code=code, latency_ms=latency)
        self._log_call(state, status="failed", latency_ms=latency, error=_failure_text(exc),
                       cache_hit=False)  # fmt: skip

    async def _record(
        self, state: _CallState, status: CallStatus, *, error_code: str | None, latency_ms: int
    ) -> None:
        if self.sink is None:
            return
        ctx, spec = state.ctx, state.spec
        record = LlmCallRecord(
            call_id=state.call_id,
            request_id=ctx.request_id,
            session_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            scope=ctx.budget_scope,
            spec_id=spec.id,
            spec_version=spec.version,
            model=state.model,
            mode=self.provider.mode,
            status=status,
            usage=state.usage,
            latency_ms=latency_ms,
            repaired=state.repaired,
            cache_hit=status == "cache_hit",
            cassette_key=state.cassette_key,
            prompt_hash=state.prompt_hash,
            error_code=error_code,
            at=self.clock.now(),
        )
        try:
            await self.sink.record(record)
        except Exception as exc:  # a broken mirror must never fail the call
            self._log.warning(
                "llm_call_sink_failed", call_id=state.call_id, error=self._safe(str(exc))
            )

    def _log_call(
        self, state: _CallState, *, status: str, latency_ms: int, error: str | None, cache_hit: bool
    ) -> None:
        ctx, usage = state.ctx, state.usage
        fields: dict[str, object] = {
            "request_id": ctx.request_id,
            "session_id": ctx.session_id,
            "call_id": state.call_id,
            "spec_id": state.spec.id,
            "model": state.model,
            "mode": self.provider.mode,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "tool_calls": usage.tool_calls,
            "cost_micros": usage.cost_micros,
            "latency_ms": latency_ms,
            "status": status,
            "repaired": state.repaired,
            "cache_hit": cache_hit,
            "cassette_key": state.cassette_key,
            "prompt_hash": state.prompt_hash,
            "attempts": state.attempts,
        }
        if state.prompt_text is not None:
            fields["prompt"] = state.prompt_text
        if error is not None:
            fields["error"] = self._safe(error)
        if status == "failed":
            self._log.warning("llm_call", **fields)
        else:
            self._log.info("llm_call", **fields)


def _effective_tools(spec: AnySpec, input: BaseModel) -> tuple[ToolSpec, ...]:
    """The spec's tools, with ``allowed_domains`` taken from the input when the spec has none.

    The web search call's input carries the policy's allowed domains (``S1Input.allowed_domains``);
    a domain list fixed on the spec wins. Anything else about the tool is untouched.
    """
    domains = getattr(input, "allowed_domains", None)
    if not spec.tools or not isinstance(domains, list) or not domains:
        return spec.tools
    wanted = [str(d) for d in cast("list[object]", domains)]
    return tuple(
        t if t.allowed_domains else t.model_copy(update={"allowed_domains": wanted})
        for t in spec.tools
    )


def _error_lines(exc: ValidationError, limit: int = 20) -> list[str]:
    lines = [
        f"{'.'.join(str(p) for p in err['loc']) or '$'}: {err['msg']}"
        for err in exc.errors(include_url=False, include_input=False)
    ]
    return lines[:limit]


def _repair_prompt(original_user: str, invalid_output: str, errors: list[str]) -> str:
    """The single repair call: the original request, the invalid output and what was wrong."""
    shown = invalid_output[:_PREVIOUS_OUTPUT_CHARS]
    problems = "\n".join(f"- {line}" for line in errors[:20])
    return (
        f"{original_user}\n\n---\n"
        "Your previous answer was rejected by validation. Return a corrected answer that follows "
        "the output schema exactly. Fix only what the problems list says.\n\n"
        f"Previous answer:\n{shown}\n\nProblems:\n{problems}"
    )


__all__ = [
    "MAX_ESCALATED_OUTPUT_TOKENS",
    "Gateway",
    "GatewayConfig",
    "RenderedPrompt",
    "escalate_cap",
]
