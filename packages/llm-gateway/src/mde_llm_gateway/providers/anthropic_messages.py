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
* the API refuses a schema with more than 16 union-typed (nullable) parameters or 24 optional ones; the strict
  schema makes every optional field ``anyOf [X, null]``, so when there are too many the provider turns just
  enough of them into plain optional properties and puts ``null`` back into the answer for every field the
  model left out (``restore_omitted_nulls``), so the gateway still sees a document valid for its own schema;
* the compiler behind ``output_config.format`` also refuses a schema that is simply too large ("compiled grammar
  is too large"), whatever its union count: `extract.data` is such a schema. The provider then makes the call
  WITHOUT constrained decoding, once, with the schema written into the instructions, remembers that this schema
  needs it (by ``schema_hash``, for the life of the provider) and strips markdown fences from the answer; the
  gateway still validates every answer locally and repairs it as for any vendor;
* ``stop_reason == "max_tokens"`` -> ``incomplete`` with the reason the gateway escalates on
  (``max_output_tokens``); ``"refusal"`` -> a refusal; anything that would need another call
  (``tool_use``, ``pause_turn``) is an error, because a provider makes exactly one call.
"""

from __future__ import annotations

import dataclasses
import json
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
# Limits the API enforces on one schema (a 400 otherwise, which lost 39 of 150 extraction calls in the first Small run).
MAX_UNION_PARAMS = 16
MAX_OPTIONAL_PARAMS = 24
# Unwrapping goes below the limit, not to it: the grammar compiler refused a schema of exactly 16 unions that it took at 14.
UNION_TARGET = 12
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
    Property NAMES are never touched (a field called ``pattern`` is a field). When the schema has more
    than ``MAX_UNION_PARAMS`` nullable parameters, the first ones in document order become optional
    properties, down to ``UNION_TARGET`` (the answer is completed with ``null`` by :func:`restore_omitted_nulls`).
    """
    out = _clean_node(dict(schema))
    unions = _count_unions(out)
    if unions > MAX_UNION_PARAMS:
        _unwrap_nullables(
            out, [max(0, min(unions - UNION_TARGET, MAX_OPTIONAL_PARAMS - _count_optionals(out)))]
        )
    return out


def _obj(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _arr(value: object) -> list[object] | None:
    return cast("list[object]", value) if isinstance(value, list) else None


def _nullable_branch(node: object) -> dict[str, object] | None:
    """The non-null branch of ``anyOf [X, null]`` (the strict schema's form of an optional value), else None."""
    branches = _arr((_obj(node) or {}).get("anyOf"))
    if branches is None or len(branches) != 2:
        return None
    objs = [_obj(b) for b in branches]
    if any(o is None for o in objs):
        return None
    pair = [o for o in objs if o is not None]
    if [o.get("type") for o in pair].count("null") != 1:
        return None
    return next(o for o in pair if o.get("type") != "null")


def _child_schemas(node: dict[str, object]) -> list[dict[str, object]]:
    kids: list[dict[str, object]] = []
    for key in _SCHEMA_MAPS:
        table = _obj(node.get(key))
        if table is not None:
            kids += [o for o in map(_obj, table.values()) if o is not None]
    for key in _SCHEMA_LISTS:
        branches = _arr(node.get(key))
        if branches is not None:
            kids += [o for o in map(_obj, branches) if o is not None]
    items = _obj(node.get("items"))
    if items is not None:
        kids.append(items)
    return kids


def _count_unions(node: dict[str, object]) -> int:
    here = 1 if isinstance(node.get("anyOf"), list) or isinstance(node.get("type"), list) else 0
    return here + sum(_count_unions(kid) for kid in _child_schemas(node))


def _required_names(node: dict[str, object]) -> set[str]:
    return {r for r in (_arr(node.get("required")) or []) if isinstance(r, str)}


def _count_optionals(node: dict[str, object]) -> int:
    props = _obj(node.get("properties"))
    here = len(set(props) - _required_names(node)) if props is not None else 0
    return here + sum(_count_optionals(kid) for kid in _child_schemas(node))


def _unwrap_nullables(node: dict[str, object], budget: list[int]) -> None:
    """Make up to ``budget[0]`` nullable properties optional, in document order (mutates the NEW tree only)."""
    props = _obj(node.get("properties"))
    if props is not None:
        for name in list(props):
            branch = _nullable_branch(props[name])
            if branch is None or budget[0] <= 0:
                continue
            wrapper = _obj(props[name]) or {}
            unwrapped = dict(branch)
            if "description" in wrapper and "description" not in unwrapped:
                unwrapped["description"] = wrapper["description"]
            props[name] = unwrapped
            if "required" in node:  # a new list: the caller's schema is never touched
                node["required"] = [r for r in (_arr(node["required"]) or []) if r != name]
            budget[0] -= 1
    for kid in _child_schemas(node):
        _unwrap_nullables(kid, budget)


def restore_omitted_nulls(text: str, schema: Mapping[str, object]) -> str:
    """Put ``null`` back for every nullable property the model left out (the inverse of the unwrapping above).

    ``schema`` is the gateway's ORIGINAL strict schema. Text that is not a JSON document, or that needs nothing,
    is returned unchanged; nothing else in the document is touched.
    """
    try:
        data: object = json.loads(text)
    except ValueError:
        return text
    changed: list[bool] = [False]
    fixed = _fill_nulls(data, dict(schema), changed)
    return json.dumps(fixed, ensure_ascii=False) if changed[0] else text


def _fill_nulls(value: object, schema: dict[str, object], changed: list[bool]) -> object:
    branches = _arr(schema.get("anyOf"))
    if branches is not None:  # a union: descend into the branch that fits the value's shape
        for branch in map(_obj, branches):
            if branch is None:
                continue
            kind = branch.get("type")
            if (_obj(value) is not None and kind == "object") or (
                _arr(value) is not None and kind == "array"
            ):
                return _fill_nulls(value, branch, changed)
        return value
    doc = _obj(value)
    if doc is not None:
        props = _obj(schema.get("properties"))
        for name, sub in (props or {}).items():
            sub_schema = _obj(sub)
            if sub_schema is None:
                continue
            if name in doc:
                doc[name] = _fill_nulls(doc[name], sub_schema, changed)
            elif _nullable_branch(sub_schema) is not None:
                doc[name] = None
                changed[0] = True
        return doc
    rows = _arr(value)
    items = _obj(schema.get("items"))
    if rows is not None and items is not None:
        return [_fill_nulls(row, items, changed) for row in rows]
    return value


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
def build_create_kwargs(req: ProviderRequest, *, constrained: bool = True) -> dict[str, object]:
    """The keyword arguments for ``client.messages.create`` (pure; unit-tested).

    ``constrained=False`` leaves out ``output_config.format`` and writes the schema into the system text.
    """
    output_config: dict[str, object] = {"effort": _EFFORT[req.effort]}
    system = req.system
    if req.schema is not None:
        if constrained:
            output_config["format"] = {
                "type": "json_schema",
                "schema": to_anthropic_schema(req.schema),
            }
        else:
            system = (system + "\n\n" if system else "") + _PROMPTED_JSON.format(
                schema=json.dumps(req.schema, ensure_ascii=False, separators=(",", ":"))
            )
    kwargs: dict[str, object] = {
        "model": req.model,
        "messages": [{"role": "user", "content": req.user}],
        "max_tokens": req.max_output_tokens,
        "output_config": output_config,
        "timeout": req.timeout_s,
    }
    if system:
        kwargs["system"] = system
    return kwargs


_PROMPTED_JSON = (
    "Answer with ONE JSON document and nothing else: no prose, no markdown fence. It must be valid for this "
    "JSON Schema, with every property present (use null where the schema allows null and you have no value):\n"
    "{schema}"
)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def unfence_json(text: str) -> str:
    """A JSON document out of a reply that may be wrapped in a markdown fence or in a sentence (no change if it already
    parses, or if no document can be found)."""
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    fenced = _FENCE.match(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start : end + 1]
    try:
        json.loads(candidate)
    except ValueError:
        return text
    return candidate


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
_SCHEMA_REFUSED = re.compile(
    r"compiled grammar|too many parameters|schemas? contains?", re.IGNORECASE
)
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
        self._prompted: set[str] = (
            set()
        )  # schema hashes the API refused to compile: sent as instructions instead
        register_secrets(settings.secret_values())

    async def _create(self, request: ProviderRequest, *, constrained: bool) -> Message:
        """One ``messages.create``; SDK failures become :class:`ProviderCallError`, other exceptions pass through."""
        kwargs = build_create_kwargs(request, constrained=constrained)
        try:
            return await self._client.messages.create(**kwargs)
        except ProviderCallError:
            raise
        except Exception as exc:
            if isinstance(exc, (anthropic.AnthropicError, OSError, TimeoutError)):
                raise map_error(exc) from None
            raise

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.tools:
            raise ProviderCallError(
                "this provider does not serve specs with tools yet; route this spec to a model "
                "whose provider does (the call was not made)",
                retryable=False,
            )
        constrained = request.schema is not None and request.schema_hash not in self._prompted
        try:
            msg = await self._create(request, constrained=constrained)
        except ProviderCallError as exc:
            if not (constrained and exc.status == 400 and _SCHEMA_REFUSED.search(str(exc))):
                raise
            # The API cannot compile this schema: one call without constrained decoding, remembered per schema.
            self._prompted.add(request.schema_hash)
            msg = await self._create(request, constrained=False)
        try:
            parsed = parse_response(msg)
            if request.schema is not None and request.schema_hash in self._prompted:
                parsed = dataclasses.replace(parsed, text=unfence_json(parsed.text))
            if (
                request.schema is not None
                and parsed.status == "completed"
                and parsed.refusal is None
            ):
                text = restore_omitted_nulls(parsed.text, request.schema)
                if text != parsed.text:
                    parsed = dataclasses.replace(parsed, text=text)
            return parsed
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
