"""OpenAIResponsesProvider against fixtures built from the REAL installed SDK types.

Responses are produced with ``openai.types.responses.Response.model_validate(<dict>)`` so the
parsing code runs against the genuine SDK models. The client is a stub: no network, no key.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest
from openai.types.responses import Response

from mde_contracts.gateway import Citation, WebSearchTool
from mde_core.clock import FakeClock
from mde_core.errors import BudgetExceeded, ProviderError, StructuredOutputError
from mde_core.settings import Settings
from mde_llm_gateway.gateway import MAX_ESCALATED_OUTPUT_TOKENS, GatewayConfig
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.providers import ProviderCallError, ProviderRequest
from mde_llm_gateway.providers.openai_responses import (
    OpenAIResponsesProvider,
    build_create_kwargs,
    map_error,
    parse_response,
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


def live_settings() -> Settings:
    return make_settings(
        llm_mode="live", allow_live_llm=True, forbid_live=False, openai_api_key=fake_api_key()
    )


def make_response(
    *,
    text: str | None = None,
    status: str = "completed",
    usage: dict[str, object] | None = None,
    extra_output: list[dict[str, object]] | None = None,
    annotations: list[dict[str, object]] | None = None,
    content: list[dict[str, object]] | None = None,
    incomplete_reason: str | None = None,
    error: dict[str, object] | None = None,
    tool_usage: dict[str, object] | None = None,
    phase: str | None = None,
) -> Response:
    parts = content
    if parts is None:
        parts = [{"type": "output_text", "text": text or "", "annotations": annotations or []}]
    message: dict[str, object] = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": parts,
    }
    if phase:
        message["phase"] = phase
    data: dict[str, object] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_700_000_000.0,
        "model": "gpt-6-luna",
        "status": status,
        "output": [*(extra_output or []), message],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage,
        "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
        "error": error,
        "tool_usage": tool_usage,
    }
    return Response.model_validate(data)


def usage_dict(
    input_tokens: int = 100, cached: int = 0, output_tokens: int = 50, reasoning: int = 0
) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": input_tokens + output_tokens,
    }


def search_item(
    item_id: str = "ws_1", action: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "id": item_id,
        "type": "web_search_call",
        "status": "completed",
        "action": action or {"type": "search", "query": "q", "sources": []},
    }


class StubResponses:
    def __init__(self, script: list[Response | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubClient:
    def __init__(self, *script: Response | Exception) -> None:
        self._responses = StubResponses(list(script))

    @property
    def responses(self) -> StubResponses:
        return self._responses


def _request(spec_id: str = "T1.echo", **kw: object) -> ProviderRequest:
    spec = ECHO_SPEC if spec_id == "T1.echo" else SEARCH_SPEC
    schema = (
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        if spec.output_model
        else None
    )
    base = ProviderRequest(
        spec=spec,
        input=EchoIn(question="q") if spec.output_model else SearchIn(query="q"),
        model="gpt-6-luna",
        system="SYSTEM",
        user="USER",
        effort="low",
        max_output_tokens=321,
        tools=spec.tools,
        schema=schema,
        schema_name="T1_echo",
        schema_hash="h",
        timeout_s=12.5,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- request building
def test_structured_request_shape() -> None:
    kwargs = build_create_kwargs(_request())
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["instructions"] == "SYSTEM" and kwargs["input"] == "USER"
    assert kwargs["max_output_tokens"] == 321 and kwargs["store"] is False
    assert kwargs["reasoning"] == {"effort": "low"} and kwargs["timeout"] == 12.5
    assert kwargs["text"] == {
        "format": {
            "type": "json_schema",
            "name": "T1_echo",
            "strict": True,
            "schema": _request().schema,
        }
    }
    assert "tools" not in kwargs and "max_tool_calls" not in kwargs and "include" not in kwargs
    assert kwargs["prompt_cache_key"] == "T1.echo:v1"


def test_web_search_request_shape_and_domain_normalisation() -> None:
    tool = WebSearchTool(
        max_tool_calls=3,
        search_context_size="medium",
        allowed_domains=["https://Example.com/path", "example.com", " docs.python.org "],
    )
    kwargs = build_create_kwargs(_request("T4.search", tools=(tool,)))
    assert kwargs["tools"] == [
        {
            "type": "web_search",
            "search_context_size": "medium",
            "filters": {"allowed_domains": ["Example.com", "example.com", "docs.python.org"]},
        }
    ]
    assert kwargs["max_tool_calls"] == 3 and kwargs["tool_choice"] == "required"
    assert kwargs["include"] == ["web_search_call.action.sources"]
    assert "text" not in kwargs  # free text: no response format
    bare = build_create_kwargs(_request("T4.search"), include_sources=False)
    assert "filters" not in bare["tools"][0] and "include" not in bare  # type: ignore[index]


def test_the_request_is_accepted_by_the_real_sdk_signature() -> None:
    """Every kwarg we send is a real parameter of ``AsyncResponses.create`` (no typos)."""
    import inspect

    from openai.resources.responses import AsyncResponses

    params = set(inspect.signature(AsyncResponses.create).parameters)
    for req in (_request(), _request("T4.search")):
        assert set(build_create_kwargs(req)) <= params


# ---------------------------------------------------------------------------- parsing
async def test_structured_response_usage_with_cached_and_reasoning_tokens() -> None:
    client = StubClient(
        make_response(text=GOOD.model_dump_json(), usage=usage_dict(1000, 400, 300, 200))
    )
    provider = OpenAIResponsesProvider(client, settings=live_settings())
    out = await provider.complete(_request())
    assert EchoOut.model_validate_json(out.text) == GOOD
    assert out.usage is not None
    assert (out.usage.input_tokens, out.usage.cached_input_tokens) == (1000, 400)
    assert (out.usage.output_tokens, out.usage.reasoning_tokens) == (300, 200)
    assert out.status == "completed" and out.search_calls == 0 and out.response_id == "resp_1"
    assert client.responses.calls[0]["model"] == "gpt-6-luna"


def test_web_search_response_counts_calls_collects_citations_and_sources() -> None:
    resp = make_response(
        text="Answer [1].",
        usage=usage_dict(3000, 0, 120),
        extra_output=[
            {"id": "rs_1", "type": "reasoning", "summary": []},
            search_item(
                "ws_1",
                {
                    "type": "search",
                    "query": "q",
                    "sources": [
                        {"type": "url", "url": "https://a.example/1"},
                        {"type": "url", "url": "https://b.example/2"},
                    ],
                },
            ),
            search_item("ws_2", {"type": "open_page", "url": "https://a.example/1"}),
        ],
        annotations=[
            {
                "type": "url_citation",
                "url": "https://a.example/1",
                "title": "A",
                "start_index": 7,
                "end_index": 10,
            },
            {
                "type": "url_citation",
                "url": "https://a.example/1",
                "title": "A",
                "start_index": 7,
                "end_index": 10,
            },
            {"type": "file_citation", "file_id": "f", "filename": "x", "index": 0},
        ],
        tool_usage={
            "image_gen": {
                "input_tokens": 0,
                "input_tokens_details": {"image_tokens": 0, "text_tokens": 0},
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "web_search": {"num_requests": 1},
        },
    )
    out = parse_response(resp)
    assert out.text == "Answer [1]."
    assert out.search_calls == 2  # two web_search_call items beat tool_usage's 1 (conservative)
    assert out.usage is not None and out.usage.tool_calls == 2
    assert out.citations == [
        Citation(url="https://a.example/1", title="A", start=7, end=10),  # deduplicated inline
        Citation(url="https://b.example/2"),  # extra source from the include option
    ]


def test_tool_usage_can_raise_the_search_count() -> None:
    resp = make_response(
        text="x",
        usage=usage_dict(),
        tool_usage={
            "image_gen": {
                "input_tokens": 0,
                "input_tokens_details": {"image_tokens": 0, "text_tokens": 0},
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "web_search": {"num_requests": 4},
        },
    )
    assert parse_response(resp).search_calls == 4


def test_only_final_answer_messages_are_used_when_phases_are_present() -> None:
    resp = make_response(
        text="the final answer",
        phase="final_answer",
        usage=usage_dict(),
        extra_output=[
            {
                "id": "msg_0",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "let me search", "annotations": []}],
            }
        ],
    )
    assert parse_response(resp).text == "the final answer"


def test_refusal_and_incomplete_and_missing_usage() -> None:
    refused = parse_response(
        make_response(content=[{"type": "refusal", "refusal": "no can do"}], usage=usage_dict())
    )
    assert refused.refusal == "no can do" and refused.text == ""
    cut = parse_response(
        make_response(
            text='{"a"',
            status="incomplete",
            incomplete_reason="max_output_tokens",
            usage=usage_dict(),
        )
    )
    assert cut.status == "incomplete" and cut.incomplete_reason == "max_output_tokens"
    assert parse_response(make_response(text="x", usage=None)).usage is None


@pytest.mark.parametrize(
    ("code", "retryable"),
    [("server_error", True), ("rate_limit_exceeded", True), ("invalid_prompt", False)],
)
def test_failed_responses_raise_with_partial_usage(code: str, retryable: bool) -> None:
    resp = make_response(
        text="",
        status="failed",
        usage=usage_dict(500, 0, 0),
        error={"code": code, "message": "nope"},
    )
    with pytest.raises(ProviderCallError) as exc:
        parse_response(resp)
    assert exc.value.retryable is retryable
    assert exc.value.usage is not None and exc.value.usage.input_tokens == 500


def test_cancelled_or_in_progress_responses_are_errors_not_answers() -> None:
    for status in ("cancelled", "in_progress", "queued"):
        with pytest.raises(ProviderCallError, match=status):
            parse_response(make_response(text="", status=status, usage=None))


# ---------------------------------------------------------------------------- error mapping
def _response(status: int, **headers: str) -> httpx2.Response:
    return httpx2.Response(
        status, headers=headers, request=httpx2.Request("POST", "https://api.example/v1/responses")
    )


def test_status_errors_map_to_retryable_or_not() -> None:
    cases = [
        (
            openai.RateLimitError(
                "slow", response=_response(429, **{"retry-after": "3"}), body=None
            ),
            True,
            3.0,
        ),
        (
            openai.RateLimitError(
                "slow", response=_response(429, **{"retry-after-ms": "1500"}), body=None
            ),
            True,
            1.5,
        ),
        (openai.InternalServerError("oops", response=_response(503), body=None), True, None),
        (openai.APIStatusError("timeout", response=_response(408), body=None), True, None),
        (openai.BadRequestError("bad", response=_response(400), body=None), False, None),
        (openai.AuthenticationError("auth", response=_response(401), body=None), False, None),
        (openai.NotFoundError("nf", response=_response(404), body=None), False, None),
    ]
    for exc, retryable, retry_after in cases:
        mapped = map_error(exc)
        assert mapped.retryable is retryable and mapped.status == exc.status_code
        assert mapped.retry_after == retry_after
        assert not mapped.maybe_billed


def test_timeouts_and_connection_errors_are_retryable_and_maybe_billed() -> None:
    req = httpx2.Request("POST", "https://api.example/v1/responses")
    for exc in (
        openai.APITimeoutError(request=req),
        openai.APIConnectionError(request=req),
        TimeoutError(),
        OSError("reset"),
    ):
        mapped = map_error(exc)
        assert mapped.retryable and mapped.maybe_billed and mapped.status is None


def test_unknown_sdk_errors_are_not_retried_and_messages_are_scrubbed() -> None:
    secret = "sk-" + "proj-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Jh1"
    mapped = map_error(openai.OpenAIError(f"weird failure with {secret}"))
    assert not mapped.retryable and secret not in str(mapped) and "[REDACTED]" in str(mapped)
    odd = map_error(
        openai.APIStatusError(f"Incorrect API key {secret}", response=_response(401), body=None)
    )
    assert secret not in str(odd)
    bad_header = map_error(
        openai.RateLimitError("x", response=_response(429, **{"retry-after": "soon"}), body=None)
    )
    assert bad_header.retry_after is None
    assert (
        map_error(
            openai.RateLimitError(
                "x", response=_response(429, **{"retry-after": "99999"}), body=None
            )
        ).retry_after
        == 300.0
    )


async def test_provider_maps_sdk_exceptions_but_lets_programming_errors_through() -> None:
    provider = OpenAIResponsesProvider(
        StubClient(
            openai.RateLimitError("slow", response=_response(429), body=None), RuntimeError("bug")
        ),
        settings=live_settings(),
    )
    with pytest.raises(ProviderCallError) as exc:
        await provider.complete(_request())
    assert exc.value.status == 429 and exc.value.retryable
    with pytest.raises(RuntimeError):
        await provider.complete(_request())


# ---------------------------------------------------------------------------- through the gateway
def _live_gateway(client: StubClient, tmp_path: Path, clock: FakeClock, **cfg: object):
    settings = live_settings()
    provider = OpenAIResponsesProvider(client, settings=settings)
    ledger = FileLedger(tmp_path / "ledger.jsonl", clock=clock)
    config = GatewayConfig(
        retry=RetryPolicy(base_delay=1.0, jitter=0.0, max_attempts=3),
        **cfg,  # type: ignore[arg-type]
    )
    return build_test_gateway(
        provider, clock=clock, ledger=ledger, settings=settings, config=config
    ), ledger


async def test_gateway_bills_real_usage_to_the_file_ledger(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        make_response(text=GOOD.model_dump_json(), usage=usage_dict(2000, 500, 400, 300))
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(scope="session:s1"))
    # 1500 uncached x 0.10 + 500 cached x 0.01 + 400 output x 0.50 = 150 + 5 + 200
    assert result.usage.cost_micros == 355 and result.mode == "live"
    assert result.usage.reasoning_tokens == 300 and result.usage.cached_input_tokens == 500
    snap = ledger.peek()
    assert snap.spent_micros == 355 and snap.live_calls == 1 and snap.reserved_micros == 0
    sent = client.responses.calls[0]
    assert sent["max_output_tokens"] == 300 and sent["reasoning"] == {"effort": "low"}
    assert sent["store"] is False


async def test_gateway_web_search_cost_includes_the_per_call_fee(
    tmp_path: Path, clock: FakeClock
) -> None:
    resp = make_response(
        text="answer",
        usage=usage_dict(4000, 0, 200),
        extra_output=[search_item("ws_1"), search_item("ws_2")],
        annotations=[
            {
                "type": "url_citation",
                "url": "https://x.example",
                "title": "X",
                "start_index": 0,
                "end_index": 6,
            }
        ],
    )
    gw, ledger = _live_gateway(StubClient(resp), tmp_path, clock)
    result = await gw.run(
        SEARCH_SPEC, SearchIn(query="q", language="en"), make_ctx(scope="research:r1")
    )
    assert result.search_calls == 2 and result.text == "answer"
    assert result.usage.cost_micros == 400 + 100 + 2 * 10_000
    assert [c.url for c in result.citations] == ["https://x.example"]
    assert ledger.peek().spent_by_scope == {"research:r1": 20_500}


async def test_gateway_retries_a_429_with_retry_after_then_succeeds(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        openai.RateLimitError("slow", response=_response(429, **{"retry-after": "4"}), body=None),
        make_response(text=GOOD.model_dump_json(), usage=usage_dict()),
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.status == "ok" and clock.sleeps == [4.0]
    kinds = [
        e["event"]
        for e in map(__import__("json").loads, (tmp_path / "ledger.jsonl").read_text().splitlines())
    ]
    assert kinds == ["reserve", "release", "reserve", "commit"]
    assert ledger.peek().reserved_micros == 0


async def test_gateway_does_not_retry_a_400_and_commits_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(openai.BadRequestError("bad schema", response=_response(400), body=None))
    gw, ledger = _live_gateway(client, tmp_path, clock)
    with pytest.raises(ProviderError) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert exc.value.status == 400 and len(client.responses.calls) == 1
    assert ledger.peek().spent_micros == 0 and ledger.peek().reserved_micros == 0


async def test_gateway_commits_usage_of_a_failed_response(tmp_path: Path, clock: FakeClock) -> None:
    resp = make_response(
        text="",
        status="failed",
        usage=usage_dict(1000, 0, 0),
        error={"code": "invalid_prompt", "message": "m"},
    )
    gw, ledger = _live_gateway(StubClient(resp), tmp_path, clock)
    with pytest.raises(ProviderError):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert ledger.peek().spent_micros == 100  # the 1000 input tokens were billed


async def test_gateway_repairs_invalid_json_from_the_api_once(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        make_response(text='{"answer": "x", "confidence": 0.5, "note": null}', usage=usage_dict()),
        make_response(text=GOOD.model_dump_json(), usage=usage_dict()),
    )
    gw, _ = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.repaired and len(client.responses.calls) == 2
    assert client.responses.calls[1]["reasoning"] == {"effort": "low"}
    again = StubClient(*[make_response(text="nope", usage=usage_dict()) for _ in range(2)])
    gw2, _ = _live_gateway(again, tmp_path, clock)
    with pytest.raises(StructuredOutputError):
        await gw2.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())


async def test_gateway_escalates_an_incomplete_response(tmp_path: Path, clock: FakeClock) -> None:
    client = StubClient(
        make_response(
            text='{"answer"',
            status="incomplete",
            incomplete_reason="max_output_tokens",
            usage=usage_dict(100, 0, 300, 300),
        ),
        make_response(text=GOOD.model_dump_json(), usage=usage_dict()),
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert [c["max_output_tokens"] for c in client.responses.calls] == [300, 600]
    assert result.usage.output_tokens == 300 + 50  # the truncated attempt was billed too
    assert ledger.peek().spent_micros == result.usage.cost_micros


async def test_sdk_timeout_is_a_billed_retryable_failure(tmp_path: Path, clock: FakeClock) -> None:
    req = httpx2.Request("POST", "https://api.example/v1/responses")
    client = StubClient(
        openai.APITimeoutError(request=req),
        make_response(text=GOOD.model_dump_json(), usage=usage_dict()),
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    result = await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.status == "ok"
    ok_only = 10 + 25  # the successful attempt alone: 100 in x 0.10 + 50 out x 0.50
    assert result.usage.cost_micros > ok_only  # the timed-out attempt was booked too
    assert ledger.peek().spent_micros == result.usage.cost_micros


async def test_an_unreadable_but_billed_response_still_books_its_usage(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The call succeeded but the SDK shape surprised us: the cost must not be lost."""
    usage = SimpleNamespace(
        input_tokens=500,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens=0,
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    weird = SimpleNamespace(output=[], usage=usage, tool_usage=None, status="completed", id="r1")
    gw, ledger = _live_gateway(StubClient(weird), tmp_path, clock)  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="could not parse"):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert ledger.peek().spent_micros == 50  # 500 input tokens x 0.10
    assert ledger.peek().reserved_micros == 0


async def test_an_unreadable_response_without_usage_books_the_estimated_input(
    tmp_path: Path, clock: FakeClock
) -> None:
    weird = SimpleNamespace(output=None, usage=None, tool_usage=None, status="completed", id="r2")
    gw, ledger = _live_gateway(StubClient(weird), tmp_path, clock)  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="could not parse"):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert ledger.peek().spent_micros > 0


# ---------------------------------------------------------------------------- reasoning eats the cap
# Reasoning tokens count against ``max_output_tokens``. L0 only saw trivial prompts (0 reasoning
# tokens), so the first heavy calls of L1 to L3 may come back ``incomplete``: the retry must stay
# bounded and metered, and a hopeless call must end in StructuredOutputError (the engine's fallback).
def reasoning_exhausted(cap: int, *, input_tokens: int = 100) -> Response:
    """What the API returns when reasoning consumed the WHOLE ``max_output_tokens`` budget: status
    ``incomplete`` / ``max_output_tokens``, a reasoning item, no answer, reasoning == output tokens."""
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 1_700_000_000.0,
            "model": "gpt-6-luna",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"id": "rs_1", "type": "reasoning", "summary": []}],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": usage_dict(input_tokens, 0, output_tokens=cap, reasoning=cap),
            "error": None,
            "tool_usage": None,
        }
    )


def _ledger_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_response_whose_reasoning_ate_the_whole_cap_parses_as_incomplete_without_text() -> None:
    parsed = parse_response(reasoning_exhausted(300))
    assert parsed.status == "incomplete" and parsed.incomplete_reason == "max_output_tokens"
    assert parsed.text == "" and parsed.refusal is None
    assert parsed.usage is not None
    assert parsed.usage.output_tokens == parsed.usage.reasoning_tokens == 300


async def test_a_trivially_small_cap_on_a_reasoning_heavy_call_falls_back_instead_of_looping(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(
        reasoning_exhausted(300), reasoning_exhausted(600), reasoning_exhausted(300)
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    with pytest.raises(StructuredOutputError) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert "cut off (max_output_tokens)" in str(exc.value.details["errors"])
    # one try, ONE higher-cap retry, and no third call: ECHO runs at effort low, so a repair could
    # not give the model more room than the two attempts that just ran out
    assert [c["max_output_tokens"] for c in client.responses.calls] == [300, 600]
    assert len(client.responses.script) == 1  # the third scripted answer was never requested
    snap = ledger.peek()
    # both truncated attempts were billed at what the API reported: (100 in x 0.10 + 300 out x 0.50)
    # + (100 in x 0.10 + 600 out x 0.50) = 160 + 310 micros; nothing is left reserved
    assert snap.spent_micros == 470 and snap.reserved_micros == 0 and snap.live_calls == 2


async def test_the_escalated_retry_reserves_again_with_its_own_larger_worst_case(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(reasoning_exhausted(300), reasoning_exhausted(600))
    gw, ledger = _live_gateway(client, tmp_path, clock)
    with pytest.raises(StructuredOutputError):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    events = _ledger_events(tmp_path / "ledger.jsonl")
    assert [e["event"] for e in events] == ["reserve", "commit", "reserve", "commit"]
    first, second = events[0]["worst_case_micros"], events[2]["worst_case_micros"]
    assert isinstance(first, int) and isinstance(second, int)
    assert second - first >= 150  # 300 more output tokens at USD 0.50 per 1M tokens
    assert first >= 160 and second >= 310  # a worst case is never below what was then billed
    assert events[0]["reservation_id"] != events[2]["reservation_id"]
    assert ledger.peek().reserved_micros == 0


async def test_the_escalated_retry_is_refused_when_the_budget_cannot_cover_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    client = StubClient(reasoning_exhausted(300), reasoning_exhausted(600))
    settings = live_settings()
    ledger = FileLedger(tmp_path / "ledger.jsonl", clock=clock)
    gw = build_test_gateway(
        OpenAIResponsesProvider(client, settings=settings),
        clock=clock,
        ledger=ledger,
        settings=settings,
        cap_usd="0.0004",  # effective cap 380 micros: fits the first call, not the first + retry
    )
    with pytest.raises(BudgetExceeded):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert len(client.responses.calls) == 1  # the retry never left the building
    snap = ledger.peek()
    assert snap.spent_micros == 160 and snap.reserved_micros == 0  # only the real first call


@pytest.mark.parametrize(
    ("ceiling", "expected_caps"),
    [
        (400, [300, 400]),  # the retry is cut to the ceiling
        (300, [300]),  # no room above the spec's own cap: no retry at all
        (10**9, [300, 600]),  # a huge setting cannot lift the hard module ceiling, nor 2x the cap
    ],
)
async def test_the_escalated_cap_never_exceeds_the_configured_ceiling(
    tmp_path: Path, clock: FakeClock, ceiling: int, expected_caps: list[int]
) -> None:
    client = StubClient(*[reasoning_exhausted(cap) for cap in expected_caps])
    gw, _ = _live_gateway(client, tmp_path, clock, max_escalated_output_tokens=ceiling)
    with pytest.raises(StructuredOutputError):
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert [c["max_output_tokens"] for c in client.responses.calls] == expected_caps
    assert max(expected_caps) <= MAX_ESCALATED_OUTPUT_TOKENS


async def test_a_higher_effort_spec_gets_one_low_effort_repair_and_then_falls_back(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Three attempts at most (try, escalated retry, repair), each reserved and billed."""
    spec = dataclasses.replace(ECHO_SPEC, reasoning_effort="high")
    client = StubClient(
        reasoning_exhausted(300), reasoning_exhausted(600), reasoning_exhausted(300)
    )
    gw, ledger = _live_gateway(client, tmp_path, clock)
    with pytest.raises(StructuredOutputError):
        await gw.run(spec, EchoIn(question="q"), make_ctx())
    sent = client.responses.calls
    assert [c["max_output_tokens"] for c in sent] == [300, 600, 300]
    efforts = [c["reasoning"] for c in sent]
    assert efforts == [{"effort": "high"}, {"effort": "high"}, {"effort": "low"}]
    kinds = [e["event"] for e in _ledger_events(tmp_path / "ledger.jsonl")]
    assert kinds == ["reserve", "commit"] * 3
    assert ledger.peek().spent_micros == 160 + 310 + 160 and ledger.peek().reserved_micros == 0


def test_stub_client_script_is_consumed_in_order() -> None:
    assert (
        asyncio.run(StubResponses([make_response(text="a", usage=None)]).create()).output_text
        == "a"
    )
