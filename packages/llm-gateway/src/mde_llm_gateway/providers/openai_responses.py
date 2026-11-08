"""OpenAIResponsesProvider: the real thing (Responses API, never Chat Completions).

The ``AsyncOpenAI`` client is INJECTED (tests pass a stub that returns real SDK ``Response``
objects; the factory builds the real client with ``max_retries=0`` because the gateway owns retries
and every attempt must be metered). One call here is one ``responses.create``.

Request shape (what is confirmed against the installed SDK types vs. what only the live smoke test
can confirm is listed in ``docs/architecture/llm-gateway.md``)::

    model, instructions=<system>, input=<user>, max_output_tokens, store=False, timeout,
    reasoning={"effort": ...},
    text={"format": {"type": "json_schema", "name": ..., "strict": True, "schema": ...}},   # structured
    tools=[{"type": "web_search", "search_context_size": ..., "filters": {"allowed_domains": [...]}}],
    max_tool_calls=N, include=["web_search_call.action.sources"], tool_choice="required"       # S1

Parsing:
* ``usage.input_tokens`` INCLUDES cached tokens (``input_tokens_details.cached_tokens``);
  ``usage.output_tokens`` INCLUDES reasoning tokens (``output_tokens_details.reasoning_tokens``);
* billable searches = max(number of ``web_search_call`` output items, ``tool_usage.web_search.num_requests``);
* citations = ``url_citation`` annotations of the answer message(s), then any extra sources from the
  ``web_search_call.action.sources`` include;
* ``status == "incomplete"`` carries ``incomplete_details.reason`` (``max_output_tokens`` ...).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import suppress
from typing import Protocol

import openai
from openai.types.responses import Response

from mde_contracts.gateway import CallMode, Citation, ToolSpec, Usage
from mde_core.logging import register_secrets
from mde_core.redact import redact_text
from mde_core.settings import Settings
from mde_llm_gateway.gating import require_live
from mde_llm_gateway.providers.base import ProviderCallError, ProviderRequest, ProviderResponse

MAX_ALLOWED_DOMAINS = 100  # documented limit per list
_MAX_RETRY_AFTER = 300.0
_RETRYABLE_RESPONSE_ERRORS = frozenset({"server_error", "rate_limit_exceeded"})


class _ResponsesAPI(Protocol):
    async def create(self, **kwargs: object) -> Response: ...


class OpenAIClientLike(Protocol):
    """The slice of ``AsyncOpenAI`` this provider uses (so tests can inject a stub)."""

    @property
    def responses(self) -> _ResponsesAPI: ...


def _normalise_domains(domains: list[str]) -> list[str]:
    """The API wants bare domains (no scheme, no path), at most 100."""
    out: list[str] = []
    for raw in domains:
        host = re.sub(r"^https?://", "", raw.strip(), flags=re.IGNORECASE).split("/")[0].strip()
        if host and host not in out:
            out.append(host)
    return out[:MAX_ALLOWED_DOMAINS]


def build_create_kwargs(
    req: ProviderRequest, *, store: bool = False, include_sources: bool = True
) -> dict[str, object]:
    """The keyword arguments for ``client.responses.create`` (pure; unit-tested)."""
    kwargs: dict[str, object] = {
        "model": req.model,
        "instructions": req.system,
        "input": req.user,
        "max_output_tokens": req.max_output_tokens,
        "reasoning": {"effort": req.effort},
        "store": store,
        "timeout": req.timeout_s,
        "prompt_cache_key": f"{req.spec.id}:v{req.spec.version}",
    }
    if req.schema is not None:
        kwargs["text"] = {
            "format": {
                "type": "json_schema",
                "name": req.schema_name,
                "strict": True,
                "schema": req.schema,
            }
        }
    if req.tools:
        kwargs["tools"] = [_tool_param(t) for t in req.tools]
        kwargs["max_tool_calls"] = sum(t.max_tool_calls for t in req.tools)
        kwargs["tool_choice"] = "required"
        if include_sources:
            kwargs["include"] = ["web_search_call.action.sources"]
    return kwargs


def _tool_param(tool: ToolSpec) -> dict[str, object]:
    param: dict[str, object] = {
        "type": "web_search",
        "search_context_size": tool.search_context_size,
    }
    domains = _normalise_domains(tool.allowed_domains)
    if domains:
        param["filters"] = {"allowed_domains": domains}
    return param


# ----------------------------------------------------------------------------- response parsing
def usage_from_response(resp: Response, search_calls: int) -> Usage | None:
    """Token usage as reported (``None`` when the response carries none)."""
    usage = resp.usage
    if usage is None:
        return None
    return Usage(
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.input_tokens_details.cached_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
        tool_calls=search_calls,
    )


def count_search_calls(resp: Response) -> int:
    """Billable web searches: the larger of the output items and ``tool_usage`` (conservative)."""
    items = sum(1 for item in resp.output if item.type == "web_search_call")
    reported = resp.tool_usage.web_search.num_requests if resp.tool_usage is not None else 0
    return max(items, reported)


def extract_answer(resp: Response) -> tuple[str, str | None, list[Citation]]:
    """``(text, refusal, citations)`` of the answer message(s).

    When any message is labelled ``phase == "final_answer"`` only those are used (commentary written
    before a search is not the answer); otherwise every message counts.
    """
    messages = [item for item in resp.output if item.type == "message"]
    finals = [m for m in messages if m.phase == "final_answer"]
    chosen = finals or messages
    texts: list[str] = []
    refusals: list[str] = []
    citations: list[Citation] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for message in chosen:
        for part in message.content:
            if part.type == "refusal":
                refusals.append(part.refusal)
                continue
            texts.append(part.text)
            for ann in part.annotations:
                if ann.type != "url_citation":
                    continue
                key = (ann.url, ann.start_index, ann.end_index)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(
                    Citation(
                        url=ann.url,
                        title=ann.title or None,
                        start=ann.start_index,
                        end=ann.end_index,
                    )
                )
    return "".join(texts), ("\n".join(refusals) or None), citations


def extra_sources(resp: Response, known: list[Citation]) -> list[Citation]:
    """Sources the model consulted (``include`` option) that are not already inline citations."""
    have = {c.url for c in known}
    out: list[Citation] = []
    for item in resp.output:
        if item.type != "web_search_call" or item.action.type != "search":
            continue
        for source in item.action.sources or []:
            if source.url not in have:
                have.add(source.url)
                out.append(Citation(url=source.url))
    return out


def parse_response(resp: Response) -> ProviderResponse:
    """Turn an SDK ``Response`` into a :class:`ProviderResponse` (or raise on a failed one)."""
    searches = count_search_calls(resp)
    usage = usage_from_response(resp, searches)
    status = resp.status or "completed"
    if status in ("failed", "cancelled", "queued", "in_progress"):
        code = resp.error.code if resp.error is not None else None
        message = resp.error.message if resp.error is not None else f"response status {status}"
        raise ProviderCallError(
            f"response {status}: {redact_text(message)[0]}",
            retryable=code in _RETRYABLE_RESPONSE_ERRORS,
            usage=usage,
            details={"code": code, "response_id": resp.id},
        )
    text, refusal, citations = extract_answer(resp)
    citations = [*citations, *extra_sources(resp, citations)]
    reason = resp.incomplete_details.reason if resp.incomplete_details is not None else None
    return ProviderResponse(
        text=text,
        usage=usage,
        citations=citations,
        search_calls=searches,
        status="incomplete" if status == "incomplete" else "completed",
        incomplete_reason=reason,
        refusal=refusal,
        response_id=resp.id,
    )


# ----------------------------------------------------------------------------- errors
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
    """SDK exception -> :class:`ProviderCallError` (retryable only for 429, 5xx, 408, timeouts and
    connection errors; the message is scrubbed of key-like text)."""
    text = redact_text(str(exc))[0]
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)):
        return ProviderCallError(f"request timed out: {text}", retryable=True, maybe_billed=True)
    if isinstance(exc, (openai.APIConnectionError, OSError)):
        return ProviderCallError(f"connection error: {text}", retryable=True, maybe_billed=True)
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        retryable = status in (408, 429) or status >= 500
        retry_after = _retry_after_seconds(exc.response.headers) if retryable else None
        return ProviderCallError(
            f"OpenAI returned HTTP {status}: {text}",
            status=status,
            retryable=retryable,
            retry_after=retry_after,
            details={"request_id": exc.request_id},
        )
    return ProviderCallError(f"{type(exc).__name__}: {text}", retryable=False)


class OpenAIResponsesProvider:
    name = "openai-responses"
    billable = True

    def __init__(
        self,
        client: OpenAIClientLike,
        *,
        settings: Settings,
        store: bool = False,
        include_sources: bool = True,
    ) -> None:
        require_live(settings)  # LLM_MODE=live AND ALLOW_LIVE_LLM=1, never under MDE_FORBID_LIVE
        self.mode: CallMode = "live"
        self._client = client
        self._store = store
        self._include_sources = include_sources
        register_secrets(settings.secret_values())

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        kwargs = build_create_kwargs(
            request, store=self._store, include_sources=self._include_sources
        )
        try:
            resp = await self._client.responses.create(**kwargs)
        except ProviderCallError:
            raise
        except Exception as exc:
            if isinstance(exc, (openai.OpenAIError, OSError, TimeoutError)):
                raise map_error(exc) from None
            raise
        try:
            return parse_response(resp)
        except ProviderCallError:
            raise
        except Exception as exc:
            # The call succeeded (and was billed) but we cannot read it: never lose its cost.
            usage: Usage | None = None
            with suppress(Exception):
                usage = usage_from_response(resp, count_search_calls(resp))
            raise ProviderCallError(
                f"could not parse the API response ({type(exc).__name__})",
                retryable=False,
                usage=usage,
                maybe_billed=True,
            ) from None
