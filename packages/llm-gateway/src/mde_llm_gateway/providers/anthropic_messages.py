"""AnthropicMessagesProvider: Claude through the Messages API (the second vendor behind the gateway).

Same job and same shape as :mod:`mde_llm_gateway.providers.openai_responses`: the ``AsyncAnthropic``
client is INJECTED (tests pass a stub that returns real SDK ``Message`` objects; the factory builds
the real client with ``max_retries=0`` because the gateway owns retries and every attempt must be
metered). One call here is one ``messages.create``. Everything that is specific to this vendor lives
in this file and nowhere else (rule 14): the request dialect, the schema dialect, the effort names,
the usage accounting and the error mapping.

Request shape (checked against the installed SDK's ``AsyncMessages.create`` signature by a test)::

    model, system=<system>, messages=[{"role": "user", "content": <user>}], max_tokens, timeout,
    output_config={"effort": "low"|"medium"|"high",
                   "format": {"type": "json_schema", "schema": <sanitised schema>}}   # structured

What is deliberately NOT sent: ``temperature`` / ``top_p`` / ``top_k`` (a non-default value is a 400 on
the current models), an assistant prefill (a 400), ``thinking: disabled`` or a forced ``tool_choice``
(a 400 on the larger models), cache markers (no cache writes, so the price list needs no write rate),
tools (this provider does not serve tool specs yet: a spec that asks for tools is refused before any
call, see ``complete``).

Parsing:
* ``usage.input_tokens`` EXCLUDES cache reads and writes, so the gateway's shape (input includes
  cached) is rebuilt: ``input = input_tokens + cache_read + cache_creation``, ``cached = cache_read``;
* ``usage.output_tokens`` includes the model's thinking; ``output_tokens_details.thinking_tokens``
  is reported as ``reasoning_tokens`` (information only: billing already counts it as output);
* ``stop_reason == "max_tokens"`` -> ``incomplete`` with the reason the gateway escalates on
  (``max_output_tokens``); ``"refusal"`` -> a refusal; anything that would need another call
  (``tool_use``, ``pause_turn``) is an error, because a provider makes exactly one call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import suppress
from typing import Protocol, cast

import anthropic
from anthropic.types import Message

from mde_contracts.gateway import CallMode, Usage
from mde_core.logging import register_secrets
from mde_core.redact import redact_text
from mde_core.settings import Settings
from mde_llm_gateway.gating import require_live
from mde_llm_gateway.providers.base import ProviderCallError, ProviderRequest, ProviderResponse

_MAX_RETRY_AFTER = 300.0

# The gateway's effort scale has a "none"; this API has no such level, and the larger models cannot
# switch thinking off, so "none" is the lowest real level.
_EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}

# Schema keywords this API rejects (a 400 would lose the whole call). They are removed from the
# PROVIDER schema only and written into the field's description instead, so the model still sees the
# constraint; the gateway re-validates every output locally with the Pydantic model.
_DROPPED_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
    "uniqueItems",
    "pattern",
)
_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf")
_SCHEMA_MAPS = ("properties", "$defs", "definitions")


class _MessagesAPI(Protocol):
    async def create(self, **kwargs: object) -> Message: ...


class AnthropicClientLike(Protocol):
    """The slice of ``AsyncAnthropic`` this provider uses (so tests can inject a stub)."""

    @property
    def messages(self) -> _MessagesAPI: ...


# ----------------------------------------------------------------------------- schema dialect
def to_anthropic_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """The gateway's strict schema, in the subset this API accepts (pure; unit-tested).

    Numeric bounds, string length bounds, ``pattern``, ``maxItems``, ``uniqueItems`` and a
    ``minItems`` above 1 are removed and described in words; ``format`` is kept only when listed.
    Property NAMES are never touched (a field called ``pattern`` is a field).
    """
    return _clean_node(dict(schema))


def _clean_node(node: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    hints: list[str] = []
    for key, value in node.items():
        if key in _DROPPED_KEYWORDS:
            hints.append(f"{key} {value}")
        elif key == "minItems":
            if value in (0, 1):
                out[key] = value
            else:
                hints.append(f"minItems {value}")
        elif key == "format":
            if value in _FORMATS:
                out[key] = value
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {
                str(name): _clean_value(sub)
                for name, sub in cast("dict[str, object]", value).items()
            }
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            out[key] = [_clean_value(sub) for sub in cast("list[object]", value)]
        elif key == "items":
            out[key] = _clean_value(value)
        else:
            out[key] = value
    if hints:
        note = "Constraints: " + "; ".join(hints) + "."
        description = out.get("description")
        out["description"] = f"{description} {note}" if isinstance(description, str) else note
    return out


def _clean_value(value: object) -> object:
    return _clean_node(cast("dict[str, object]", value)) if isinstance(value, dict) else value


# ----------------------------------------------------------------------------- request
def build_create_kwargs(req: ProviderRequest) -> dict[str, object]:
    """The keyword arguments for ``client.messages.create`` (pure; unit-tested)."""
    output_config: dict[str, object] = {"effort": _EFFORT[req.effort]}
    if req.schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": to_anthropic_schema(req.schema)}
    kwargs: dict[str, object] = {
        "model": req.model,
        "messages": [{"role": "user", "content": req.user}],
        "max_tokens": req.max_output_tokens,
        "output_config": output_config,
        "timeout": req.timeout_s,
    }
    if req.system:
        kwargs["system"] = req.system
    return kwargs


# ----------------------------------------------------------------------------- response parsing
def usage_from_message(msg: Message) -> Usage:
    """Token usage in the gateway's shape (input INCLUDES the cached tokens)."""
    usage = msg.usage
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0
    details = usage.output_tokens_details
    return Usage(
        input_tokens=usage.input_tokens + cache_read + cache_write,
        cached_input_tokens=cache_read,
        output_tokens=usage.output_tokens,
        reasoning_tokens=min(details.thinking_tokens, usage.output_tokens) if details else 0,
        tool_calls=0,
    )


def _answer_text(msg: Message) -> str:
    return "".join(block.text for block in msg.content if block.type == "text")


def _refusal_text(msg: Message) -> str:
    details = msg.stop_details
    if details is None:
        return "the model declined to answer"
    bits = [details.explanation or "", f"category {details.category}" if details.category else ""]
    return "the model declined to answer" + "".join(f" ({b})" for b in bits if b)


def parse_response(msg: Message) -> ProviderResponse:
    """Turn an SDK ``Message`` into a :class:`ProviderResponse` (or raise on one that needs more calls)."""
    usage = usage_from_message(msg)
    stop = msg.stop_reason
    if stop in ("tool_use", "pause_turn"):
        raise ProviderCallError(
            f"the model stopped with {stop!r}, which needs a second call this provider does not make",
            retryable=False,
            usage=usage,
            details={"response_id": msg.id},
        )
    if stop == "refusal":
        return ProviderResponse(
            text=_answer_text(msg),
            usage=usage,
            refusal=_refusal_text(msg),
            response_id=msg.id,
        )
    if stop in ("max_tokens", "model_context_window_exceeded"):
        return ProviderResponse(
            text=_answer_text(msg),
            usage=usage,
            status="incomplete",
            incomplete_reason="max_output_tokens" if stop == "max_tokens" else stop,
            response_id=msg.id,
        )
    return ProviderResponse(text=_answer_text(msg), usage=usage, response_id=msg.id)


# ----------------------------------------------------------------------------- errors
_BILLING_WORDS = re.compile(r"credit balance|billing|spend limit|insufficient", re.IGNORECASE)


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """``Retry-After-Ms`` or ``Retry-After`` (seconds); an HTTP-date is ignored."""
    try:
        if (ms := headers.get("retry-after-ms")) is not None:
            return min(max(float(ms) / 1000.0, 0.0), _MAX_RETRY_AFTER)
        if (sec := headers.get("retry-after")) is not None:
            return min(max(float(sec), 0.0), _MAX_RETRY_AFTER)
    except ValueError:
        return None
    return None


def map_error(exc: Exception) -> ProviderCallError:
    """SDK exception -> :class:`ProviderCallError` (retryable only for 429, 5xx including 529 overloaded,
    408, timeouts and connection errors; the message is scrubbed of key-like text).

    A spent balance arrives as HTTP 402 or as a 400 whose message says so: it is reported as status 402,
    never retryable, and the gateway's circuit breaker counts it (one clear failure, not a storm).
    """
    text = redact_text(str(exc))[0]
    if isinstance(exc, (anthropic.APITimeoutError, TimeoutError)):
        return ProviderCallError(f"request timed out: {text}", retryable=True, maybe_billed=True)
    if isinstance(exc, (anthropic.APIConnectionError, OSError)):
        return ProviderCallError(f"connection error: {text}", retryable=True, maybe_billed=True)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status == 400 and _BILLING_WORDS.search(text):
            status = 402
        retryable = status in (408, 429) or status >= 500
        retry_after = _retry_after_seconds(exc.response.headers) if retryable else None
        return ProviderCallError(
            f"Anthropic returned HTTP {status}: {text}",
            status=status,
            retryable=retryable,
            retry_after=retry_after,
            details={"request_id": exc.request_id},
        )
    return ProviderCallError(f"{type(exc).__name__}: {text}", retryable=False)


class AnthropicMessagesProvider:
    name = "anthropic-messages"
    billable = True

    def __init__(self, client: AnthropicClientLike, *, settings: Settings) -> None:
        require_live(settings)  # LLM_MODE=live, never under MDE_FORBID_LIVE
        self.mode: CallMode = "live"
        self._client = client
        register_secrets(settings.secret_values())

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.tools:
            raise ProviderCallError(
                "this provider does not serve specs with tools yet; route this spec to a model "
                "whose provider does (the call was not made)",
                retryable=False,
            )
        kwargs = build_create_kwargs(request)
        try:
            msg = await self._client.messages.create(**kwargs)
        except ProviderCallError:
            raise
        except Exception as exc:
            if isinstance(exc, (anthropic.AnthropicError, OSError, TimeoutError)):
                raise map_error(exc) from None
            raise
        try:
            return parse_response(msg)
        except ProviderCallError:
            raise
        except Exception as exc:
            # The call succeeded (and was billed) but we cannot read it: never lose its cost.
            usage: Usage | None = None
            with suppress(Exception):
                usage = usage_from_message(msg)
            raise ProviderCallError(
                f"could not parse the API response ({type(exc).__name__})",
                retryable=False,
                usage=usage,
                maybe_billed=True,
            ) from None
