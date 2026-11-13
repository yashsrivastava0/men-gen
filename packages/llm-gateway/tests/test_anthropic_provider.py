"""AnthropicMessagesProvider against fixtures built from the REAL installed SDK types.

Messages are produced with ``anthropic.types.Message.model_validate(<dict>)`` so the parsing code
runs against the genuine SDK models. The client is a stub: no network, no key. The provider contract
(one call, usage in the gateway's shape, errors as ``ProviderCallError``) is the same one the OpenAI
provider keeps, so the checks here mirror ``test_openai_provider.py``.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from anthropic.resources.messages import AsyncMessages
from anthropic.types import Message

from mde_contracts.gateway import WebSearchTool
from mde_core.clock import FakeClock
from mde_core.errors import LiveCallForbidden, ProviderError
from mde_core.settings import Settings
from mde_llm_gateway.gateway import _STATUS_COUNTS_FOR_BREAKER, GatewayConfig
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.providers import ProviderCallError, ProviderRequest
from mde_llm_gateway.providers.anthropic_messages import (
    AnthropicMessagesProvider,
    build_create_kwargs,
    map_error,
    parse_response,
    to_anthropic_schema,
    usage_from_message,
)
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    SearchIn,
    build_test_gateway,
    fake_api_key,
    make_ctx,
    make_settings,
)

GOOD = EchoOut(answer="hello there", confidence=0.5)
HAIKU_ROUTES = {
    "fast": "claude-haiku-5-5",
    "standard": "claude-haiku-5-5",
    "deep": "claude-haiku-5-5",
}


def live_settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "llm_mode": "live",
        "forbid_live": False,
        "anthropic_api_key": fake_api_key(),
        "model_routes": HAIKU_ROUTES,
    }
    return make_settings(**{**base, **kw})


def make_message(
    *,
    text: str | None = "",
    content: list[dict[str, object]] | None = None,
    stop_reason: str | None = "end_turn",
    usage: dict[str, object] | None = None,
    stop_details: dict[str, object] | None = None,
) -> Message:
    blocks = content if content is not None else [{"type": "text", "text": text or ""}]
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": usage if usage is not None else usage_dict(),
        }
    )


def usage_dict(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int | None = None,
    cache_write: int | None = None,
    thinking: int | None = None,
) -> dict[str, object]:
    out: dict[str, object] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_read is not None:
        out["cache_read_input_tokens"] = cache_read
    if cache_write is not None:
        out["cache_creation_input_tokens"] = cache_write
    if thinking is not None:
        out["output_tokens_details"] = {"thinking_tokens": thinking}
    return out


class StubMessages:
    def __init__(self, script: list[Message | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> Message:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubClient:
    def __init__(self, *script: Message | Exception) -> None:
        self._messages = StubMessages(list(script))

    @property
    def messages(self) -> StubMessages:
        return self._messages


SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"a": {"type": "string"}},
    "required": ["a"],
    "additionalProperties": False,
}


def _request(spec_id: str = "T1.echo", **kw: object) -> ProviderRequest:
    spec = ECHO_SPEC if spec_id == "T1.echo" else SEARCH_SPEC
    base = ProviderRequest(
        spec=spec,
        input=EchoIn(question="q") if spec.output_model else SearchIn(query="q"),
        model="claude-haiku-5-5",
        system="SYSTEM",
        user="USER",
        effort="low",
        max_output_tokens=321,
        tools=spec.tools,
        schema=SCHEMA if spec.output_model else None,
        schema_name="T1_echo",
        schema_hash="h",
        timeout_s=12.5,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- request building
def test_structured_request_shape() -> None:
    kwargs = build_create_kwargs(_request())
    assert kwargs == {
        "model": "claude-haiku-5-5",
        "system": "SYSTEM",
        "messages": [{"role": "user", "content": "USER"}],
        "max_tokens": 321,
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        "timeout": 12.5,
    }


def test_every_kwarg_is_a_real_sdk_parameter_and_no_sampling_parameter_is_ever_sent() -> None:
    accepted = set(inspect.signature(AsyncMessages.create).parameters)
    for effort in ("none", "low", "medium", "high"):
        for schema in (SCHEMA, None):
            kwargs = build_create_kwargs(_request(effort=effort, schema=schema))
            assert set(kwargs) <= accepted
            # These are a 400 on the current models (or silently wrong): never part of a request.
            for banned in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "tools"):
                assert banned not in kwargs
            assert kwargs["messages"][-1]["role"] == "user"  # type: ignore[index]  # no prefill


def test_effort_names_map_onto_the_vendor_scale_and_none_is_the_lowest_level() -> None:
    sent = {
        e: build_create_kwargs(_request(effort=e))["output_config"]["effort"]  # type: ignore[index]
        for e in ("none", "low", "medium", "high")
    }
    assert sent == {"none": "low", "low": "low", "medium": "medium", "high": "high"}


def test_free_text_request_has_no_format_and_an_empty_system_is_omitted() -> None:
    kwargs = build_create_kwargs(_request(schema=None, system=""))
    assert kwargs["output_config"] == {"effort": "low"} and "system" not in kwargs


# ---------------------------------------------------------------------------- schema dialect
def test_the_schema_keeps_its_structure_and_drops_what_the_api_rejects() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["score", "tags", "when", "name"],
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Quality."},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
            "when": {"type": "string", "format": "date-time"},
            "name": {"type": "string", "pattern": "^[a-z]+$", "format": "made-up"},
        },
    }
    before = json.dumps(schema, sort_keys=True)
    out = to_anthropic_schema(schema)
    assert json.dumps(schema, sort_keys=True) == before  # the input is never mutated
    props = out["properties"]  # type: ignore[index]
    assert props["score"] == {  # type: ignore[index]
        "type": "integer",
        "description": "Quality. Constraints: minimum 0; maximum 10.",
    }
    assert props["tags"] == {  # type: ignore[index]
        "type": "array",
        "items": {"type": "string"},
        "description": "Constraints: minItems 2; maxItems 5.",
    }
    assert props["when"] == {"type": "string", "format": "date-time"}  # type: ignore[index]
    assert props["name"] == {  # type: ignore[index]
        "type": "string",
        "description": "Constraints: pattern ^[a-z]+$.",
    }
    assert out["additionalProperties"] is False and out["required"] == schema["required"]


def test_a_field_named_like_a_keyword_is_a_field_and_nested_schemas_are_cleaned() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["pattern", "items"],
        "properties": {
            "pattern": {"type": "string"},
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "anyOf": [{"type": "number", "minimum": 1}, {"type": "null"}],
                },
            },
        },
    }
    out = to_anthropic_schema(schema)
    props = out["properties"]  # type: ignore[index]
    assert "pattern" in props  # type: ignore[operator]
    items = props["items"]  # type: ignore[index]
    assert items["minItems"] == 1  # type: ignore[index]  # 0 and 1 are allowed
    assert items["items"]["anyOf"][0] == {  # type: ignore[index]
        "type": "number",
        "description": "Constraints: minimum 1.",
    }
    assert items["items"]["anyOf"][1] == {"type": "null"}  # type: ignore[index]


def test_every_registered_output_model_converts() -> None:
    """The real strict schemas of every registered spec pass through the sanitiser unchanged in shape."""
    from mde_llm_gateway.strict_schema import to_strict_schema

    strict = to_strict_schema(EchoOut)
    out = to_anthropic_schema(strict)
    assert out["type"] == "object" and out["additionalProperties"] is False
    assert set(out["properties"]) == set(strict["properties"])  # type: ignore[arg-type,call-overload]


# ---------------------------------------------------------------------------- tools
async def test_a_spec_with_tools_is_refused_before_any_call() -> None:
    client = StubClient()
    provider = AnthropicMessagesProvider(client, settings=live_settings())
    tool = WebSearchTool(max_tool_calls=1, search_context_size="low", allowed_domains=[])
    with pytest.raises(ProviderCallError, match="tools") as exc:
        await provider.complete(_request("T4.search", tools=(tool,)))
    assert not exc.value.retryable and not exc.value.maybe_billed
    assert client.messages.calls == []


# ---------------------------------------------------------------------------- response parsing
def test_plain_answer_and_usage_in_the_gateways_shape() -> None:
    resp = parse_response(make_message(text='{"a": "x"}', usage=usage_dict(1000, 400)))
    assert resp.text == '{"a": "x"}' and resp.status == "completed" and resp.refusal is None
    assert resp.usage is not None
    assert (resp.usage.input_tokens, resp.usage.cached_input_tokens) == (1000, 0)
    assert (resp.usage.output_tokens, resp.usage.reasoning_tokens) == (400, 0)
    assert resp.response_id == "msg_1" and resp.search_calls == 0 and resp.citations == []


def test_cache_tokens_are_added_into_input_because_the_vendor_reports_them_separately() -> None:
    usage = usage_from_message(
        make_message(
            text="x", usage=usage_dict(300, 80, cache_read=500, cache_write=200, thinking=60)
        )
    )
    # input_tokens EXCLUDES cache reads and writes here: 300 + 500 + 200, of which 500 were read.
    assert usage.input_tokens == 1000 and usage.cached_input_tokens == 500
    assert usage.output_tokens == 80 and usage.reasoning_tokens == 60 and usage.tool_calls == 0


def test_thinking_blocks_are_not_part_of_the_answer() -> None:
    msg = make_message(
        content=[
            {"type": "thinking", "thinking": "let me see", "signature": "sig"},
            {"type": "text", "text": '{"a": '},
            {"type": "text", "text": '"b"}'},
        ]
    )
    assert parse_response(msg).text == '{"a": "b"}'


def test_max_tokens_is_incomplete_with_the_reason_the_gateway_escalates_on() -> None:
    resp = parse_response(make_message(text='{"a": "tru', stop_reason="max_tokens"))
    assert resp.status == "incomplete" and resp.incomplete_reason == "max_output_tokens"
    assert resp.text == '{"a": "tru' and resp.usage is not None


def test_a_context_window_stop_is_incomplete_under_its_own_reason() -> None:
    resp = parse_response(make_message(text="", stop_reason="model_context_window_exceeded"))
    assert resp.status == "incomplete" and resp.incomplete_reason == "model_context_window_exceeded"


def test_a_refusal_carries_the_category_and_keeps_the_usage() -> None:
    resp = parse_response(
        make_message(
            text="",
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": "not allowed"},
        )
    )
    assert resp.refusal is not None and "not allowed" in resp.refusal and "cyber" in resp.refusal
    assert resp.usage is not None and resp.status == "completed"
    plain = parse_response(make_message(text="", stop_reason="refusal"))
    assert plain.refusal == "the model declined to answer"


@pytest.mark.parametrize("stop", ["tool_use", "pause_turn"])
def test_a_stop_that_needs_another_call_is_an_error_with_its_cost(stop: str) -> None:
    with pytest.raises(ProviderCallError, match=stop) as exc:
        parse_response(make_message(text="x", stop_reason=stop, usage=usage_dict(500, 20)))
    assert not exc.value.retryable
    assert exc.value.usage is not None and exc.value.usage.input_tokens == 500


# ---------------------------------------------------------------------------- error mapping
def _response(status: int, **headers: str) -> httpx2.Response:
    return httpx2.Response(
        status, headers=headers, request=httpx2.Request("POST", "https://api.example/v1/messages")
    )


def test_status_errors_map_to_retryable_or_not() -> None:
    cases: list[tuple[anthropic.APIStatusError, bool, float | None]] = [
        (
            anthropic.RateLimitError(
                "slow", response=_response(429, **{"retry-after": "3"}), body=None
            ),
            True,
            3.0,
        ),
        (
            anthropic.RateLimitError(
                "slow", response=_response(429, **{"retry-after-ms": "1500"}), body=None
            ),
            True,
            1.5,
        ),
        (anthropic.OverloadedError("busy", response=_response(529), body=None), True, None),
        (anthropic.InternalServerError("oops", response=_response(500), body=None), True, None),
        (anthropic.InternalServerError("oops", response=_response(503), body=None), True, None),
        (anthropic.APIStatusError("timeout", response=_response(408), body=None), True, None),
        (anthropic.BadRequestError("bad schema", response=_response(400), body=None), False, None),
        (anthropic.AuthenticationError("auth", response=_response(401), body=None), False, None),
        (anthropic.PermissionDeniedError("no", response=_response(403), body=None), False, None),
        (anthropic.NotFoundError("no model", response=_response(404), body=None), False, None),
        (anthropic.RequestTooLargeError("big", response=_response(413), body=None), False, None),
    ]
    for exc, retryable, retry_after in cases:
        mapped = map_error(exc)
        assert mapped.retryable is retryable and mapped.status == exc.status_code
        assert mapped.retry_after == retry_after
        assert not mapped.maybe_billed


def test_a_spent_balance_is_status_402_never_retried_and_counted_by_the_breaker() -> None:
    by_402 = map_error(anthropic.APIStatusError("billing", response=_response(402), body=None))
    by_400 = map_error(
        anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=_response(400),
            body=None,
        )
    )
    for mapped in (by_402, by_400):
        assert mapped.status == 402 and not mapped.retryable and mapped.retry_after is None
    assert 402 in _STATUS_COUNTS_FOR_BREAKER and 401 in _STATUS_COUNTS_FOR_BREAKER
    plain_400 = map_error(
        anthropic.BadRequestError("bad schema", response=_response(400), body=None)
    )
    assert plain_400.status == 400  # an ordinary bad request keeps its status


def test_timeouts_and_connection_errors_are_retryable_and_maybe_billed() -> None:
    req = httpx2.Request("POST", "https://api.example/v1/messages")
    for exc in (
        anthropic.APITimeoutError(request=req),
        anthropic.APIConnectionError(request=req),
        TimeoutError(),
        OSError("reset"),
    ):
        mapped = map_error(exc)
        assert mapped.retryable and mapped.maybe_billed and mapped.status is None


def test_unknown_sdk_errors_are_not_retried_and_messages_are_scrubbed() -> None:
    secret = "sk-" + "ant-" + "api03-Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Jh1Gf0"
    mapped = map_error(anthropic.AnthropicError(f"weird failure with {secret}"))
    assert not mapped.retryable and secret not in str(mapped) and "[REDACTED]" in str(mapped)
    odd = map_error(
        anthropic.APIStatusError(f"invalid x-api-key {secret}", response=_response(401), body=None)
    )
    assert secret not in str(odd)
    soon = map_error(
        anthropic.RateLimitError("x", response=_response(429, **{"retry-after": "soon"}), body=None)
    )
    assert soon.retry_after is None
    huge = map_error(
        anthropic.RateLimitError(
            "x", response=_response(429, **{"retry-after": "99999"}), body=None
        )
    )
    assert huge.retry_after == 300.0


async def test_provider_maps_sdk_exceptions_but_lets_programming_errors_through() -> None:
    provider = AnthropicMessagesProvider(
        StubClient(
            anthropic.RateLimitError("slow", response=_response(429), body=None),
            RuntimeError("bug"),
        ),
        settings=live_settings(),
    )
    with pytest.raises(ProviderCallError) as exc:
        await provider.complete(_request())
    assert exc.value.status == 429 and exc.value.retryable
    with pytest.raises(RuntimeError):
        await provider.complete(_request())


async def test_an_unreadable_response_keeps_its_cost() -> None:
    class Broken:
        usage = make_message(text="x", usage=usage_dict(900, 10)).usage
        stop_reason = "end_turn"
        id = "msg_x"

        @property
        def content(self) -> list[object]:
            raise ValueError("cannot read")

    provider = AnthropicMessagesProvider(StubClient(Broken()), settings=live_settings())  # type: ignore[arg-type]
    with pytest.raises(ProviderCallError) as exc:
        await provider.complete(_request())
    assert exc.value.maybe_billed and not exc.value.retryable
    assert exc.value.usage is not None and exc.value.usage.input_tokens == 900


# ---------------------------------------------------------------------------- gating
def test_a_provider_needs_live_mode_like_every_other_live_provider() -> None:
    with pytest.raises(LiveCallForbidden):
        AnthropicMessagesProvider(StubClient(), settings=live_settings(forbid_live=True))
    with pytest.raises(LiveCallForbidden):
        AnthropicMessagesProvider(StubClient(), settings=live_settings(llm_mode="mock"))


# ---------------------------------------------------------------------------- through the gateway
def _live_gateway(client: StubClient, tmp_path: Path, clock: FakeClock, **cfg: object):
    settings = live_settings()
    provider = AnthropicMessagesProvider(client, settings=settings)
    ledger = FileLedger(tmp_path / "ledger.jsonl", clock=clock)
    config = GatewayConfig(
        retry=RetryPolicy(base_delay=1.0, jitter=0.0, max_attempts=3),
        **cfg,  # type: ignore[arg-type]
    )
    return build_test_gateway(
        provider, clock=clock, ledger=ledger, settings=settings, config=config
    ), ledger


async def test_gateway_books_claude_usage_at_claude_prices(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        make_message(
            text=GOOD.model_dump_json(),
            usage=usage_dict(1500, 400, cache_read=500, thinking=300),
        )
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(scope="session:s1"))
    # input 1500 + 500 cache read = 2000, 500 cached: 1500 x 0.10 + 500 x 0.01 + 400 x 0.50
    assert result.usage.cost_micros == 150 + 5 + 200 and result.mode == "live"
    assert result.usage.input_tokens == 2000 and result.usage.cached_input_tokens == 500
    assert result.usage.reasoning_tokens == 300
    snap = ledger.peek()
    assert snap.spent_micros == 355 and snap.live_calls == 1 and snap.reserved_micros == 0
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["max_tokens"] == 300
    assert sent["output_config"]["effort"] == "low"  # type: ignore[index]


async def test_gateway_retries_a_529_overloaded_then_succeeds(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        anthropic.OverloadedError(
            "busy", response=_response(529, **{"retry-after": "2"}), body=None
        ),
        make_message(text=GOOD.model_dump_json()),
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.status == "ok" and clock.sleeps == [2.0]
    events = [
        json.loads(line)["event"] for line in (tmp_path / "ledger.jsonl").read_text().splitlines()
    ]
    assert events == ["reserve", "release", "reserve", "commit"]
    assert ledger.peek().reserved_micros == 0


async def test_gateway_does_not_retry_a_spent_balance_and_commits_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=_response(400),
            body=None,
        )
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    with pytest.raises(ProviderError) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert exc.value.status == 402 and len(client.messages.calls) == 1
    assert ledger.peek().spent_micros == 0 and ledger.peek().reserved_micros == 0


async def test_gateway_escalates_once_on_max_tokens_and_repairs_bad_json(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        make_message(text='{"answer": "he', stop_reason="max_tokens", usage=usage_dict(100, 300)),
        make_message(text='{"answer": "x", "confidence": 0.5, "note": null}'),
        make_message(text=GOOD.model_dump_json()),
    )
    gw, _ = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.status == "repaired" and len(client.messages.calls) == 3
    first, second = client.messages.calls[0], client.messages.calls[1]
    assert second["max_tokens"] > first["max_tokens"]  # type: ignore[operator]


# ---------------------------------------------------------------------------- the 16-union limit
def _nullable_schema(n: int, required_extra: int = 0) -> dict[str, object]:
    props: dict[str, object] = {
        f"f{i}": {"anyOf": [{"type": "string", "description": f"d{i}"}, {"type": "null"}]}
        for i in range(n)
    }
    names = list(props)
    for i in range(required_extra):
        props[f"r{i}"] = {"type": "string"}
        names.append(f"r{i}")
    return {
        "type": "object",
        "properties": props,
        "required": names,
        "additionalProperties": False,
    }


def _unions(node: object) -> int:
    from mde_llm_gateway.providers.anthropic_messages import _count_unions

    return _count_unions(node)  # type: ignore[arg-type]


def test_a_schema_within_the_union_limit_is_left_alone() -> None:
    schema = _nullable_schema(16)
    out = to_anthropic_schema(schema)
    assert out["required"] == schema["required"] and _unions(out) == 16


def test_a_schema_over_the_limit_is_unwrapped_down_to_the_target() -> None:
    schema = _nullable_schema(20, required_extra=2)
    before = json.dumps(schema, sort_keys=True)
    out = to_anthropic_schema(schema)
    assert json.dumps(schema, sort_keys=True) == before  # the caller's schema is untouched
    assert _unions(out) == 12
    required = out["required"]
    assert isinstance(required, list) and len(required) == 22 - 8
    props = out["properties"]
    assert props["f0"] == {"type": "string", "description": "d0"}  # type: ignore[index]  # unwrapped, description kept
    assert (
        "f0" not in required and "f19" in required and "r0" in required
    )  # the first eight, in order


def test_missing_nullables_are_put_back_as_null_including_inside_arrays() -> None:
    from mde_llm_gateway.providers.anthropic_messages import restore_omitted_nulls

    row = _nullable_schema(3)
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows", "note"],
        "properties": {
            "rows": {"type": "array", "items": row},
            "note": {"anyOf": [row, {"type": "null"}]},
        },
    }
    text = '{"rows": [{"f1": "x"}, {}], "note": {"f2": "y"}}'
    got = json.loads(restore_omitted_nulls(text, schema))
    assert got["rows"] == [
        {"f0": None, "f1": "x", "f2": None},
        {"f0": None, "f1": None, "f2": None},
    ]
    assert got["note"] == {"f0": None, "f1": None, "f2": "y"}
    complete = '{"rows": [], "note": null}'
    assert restore_omitted_nulls(complete, schema) == complete  # nothing to add: text unchanged
    assert restore_omitted_nulls("not json", schema) == "not json"


async def test_the_provider_returns_text_that_matches_the_original_schema(
    tmp_path: Path, clock: FakeClock
) -> None:
    schema = _nullable_schema(20)
    omitted = {
        f"f{i}": "v" for i in range(10, 20)
    }  # the model skipped the 10 unwrapped-or-not nullables f0..f9
    client = StubClient(make_message(text=json.dumps(omitted)))
    provider = AnthropicMessagesProvider(client, settings=live_settings())
    resp = await provider.complete(_request(schema=schema))
    doc = json.loads(resp.text)
    assert set(doc) == set(schema["properties"])  # type: ignore[arg-type]
    assert all(doc[f"f{i}"] is None for i in range(10)) and doc["f19"] == "v"
    sent = client.messages.calls[0]["output_config"]["format"]["schema"]  # type: ignore[index]
    assert _unions(sent) == 12


# ---------------------------------------------------------------------------- schemas the API cannot compile
GRAMMAR_REFUSED = anthropic.BadRequestError(
    "The compiled grammar is too large, which would cause performance issues. Simplify your schema.",
    response=_response(400),
    body=None,
)


def test_unconstrained_kwargs_carry_the_schema_in_the_instructions_instead_of_a_format() -> None:
    kwargs = build_create_kwargs(_request(), constrained=False)
    assert kwargs["output_config"] == {"effort": "low"}
    system = kwargs["system"]
    assert isinstance(system, str) and system.startswith("SYSTEM\n\n")
    assert json.dumps(SCHEMA, separators=(",", ":")) in system and "no markdown fence" in system
    assert "system" in build_create_kwargs(
        _request(system=""), constrained=False
    )  # schema alone is a system text


def test_unfence_json_strips_fences_and_prose_but_never_invents() -> None:
    from mde_llm_gateway.providers.anthropic_messages import unfence_json

    doc = '{"a": 1}'
    assert unfence_json(doc) == doc
    assert unfence_json("```json\n" + doc + "\n```") == doc
    assert unfence_json("Here you go:\n" + doc + "\nHope that helps") == doc
    assert unfence_json("no document here") == "no document here"
    assert unfence_json('{"a": ') == '{"a": '  # broken stays broken: the gateway repairs it


async def test_a_refused_schema_is_retried_once_without_constrained_decoding_and_remembered() -> (
    None
):
    client = StubClient(
        GRAMMAR_REFUSED,
        make_message(text='```json\n{"a": "x"}\n```'),
        make_message(text='{"a": "y"}'),
    )
    provider = AnthropicMessagesProvider(client, settings=live_settings())
    first = await provider.complete(_request())
    assert first.text == '{"a": "x"}'  # fence stripped
    calls = client.messages.calls
    assert "format" in calls[0]["output_config"] and "format" not in calls[1]["output_config"]  # type: ignore[operator]
    second = await provider.complete(
        _request()
    )  # remembered: no second attempt at constrained decoding
    assert second.text == '{"a": "y"}' and len(calls) == 3
    assert "format" not in calls[2]["output_config"]  # type: ignore[operator]


async def test_other_bad_requests_are_not_retried_unconstrained() -> None:
    client = StubClient(
        anthropic.BadRequestError("max_tokens too large", response=_response(400), body=None)
    )
    provider = AnthropicMessagesProvider(client, settings=live_settings())
    with pytest.raises(ProviderCallError) as exc:
        await provider.complete(_request())
    assert exc.value.status == 400 and len(client.messages.calls) == 1


async def test_a_schema_that_fails_even_unconstrained_surfaces_the_error() -> None:
    client = StubClient(
        GRAMMAR_REFUSED, anthropic.BadRequestError("bad key", response=_response(400), body=None)
    )
    provider = AnthropicMessagesProvider(client, settings=live_settings())
    with pytest.raises(ProviderCallError) as exc:
        await provider.complete(_request())
    assert exc.value.status == 400 and len(client.messages.calls) == 2
