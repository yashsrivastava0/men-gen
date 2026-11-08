"""MockProvider and ScriptedProvider (the offline providers)."""

from __future__ import annotations

import dataclasses

import pytest

from mde_contracts.gateway import Usage
from mde_core.clock import FakeClock
from mde_core.errors import ConfigError
from mde_llm_gateway.providers import (
    NO_USAGE,
    MockProvider,
    ProviderCallError,
    ProviderRequest,
    ScriptedProvider,
    ScriptedResponse,
    ScriptExhausted,
)
from mde_llm_gateway.providers.mock import resolve_handler
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    SearchIn,
    build_test_gateway,
    make_ctx,
)


def _request(spec_id: str = "T1.echo", **kw: object) -> ProviderRequest:
    spec = ECHO_SPEC if spec_id == "T1.echo" else SEARCH_SPEC
    base = ProviderRequest(
        spec=spec,
        input=EchoIn(question="hello"),
        model="gpt-6-luna",
        system="sys",
        user="user text " * 10,
        effort="low",
        max_output_tokens=100,
        tools=spec.tools,
        schema=None,
        schema_name="x",
        schema_hash="h",
        timeout_s=5.0,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- MockProvider
async def test_mock_provider_returns_validated_json_with_small_synthetic_usage() -> None:
    response = await MockProvider().complete(_request())
    assert EchoOut.model_validate_json(response.text).answer == "echo: hello"
    assert response.usage is not None and response.usage.input_tokens > 0
    assert response.search_calls == 0 and response.status == "completed"


async def test_mock_provider_free_text_handler_returns_text_and_citations() -> None:
    req = _request("T4.search", input=SearchIn(query="pune cafes"))
    response = await MockProvider().complete(req)
    assert response.text == "Results for pune cafes"
    assert response.search_calls == 1 and response.usage is not None
    assert response.usage.tool_calls == 1
    assert [c.url for c in response.citations] == ["https://example.com/a"]


@pytest.mark.parametrize(
    "path",
    ["nocolon", ":func", "mod:", "mde_llm_gateway.nope:fn", "mde_llm_gateway.testing_specs:nope"],
)
def test_bad_handler_paths_are_config_errors(path: str) -> None:
    with pytest.raises(ConfigError):
        resolve_handler(path)


def test_handler_must_be_callable() -> None:
    with pytest.raises(ConfigError, match="not callable"):
        resolve_handler("mde_llm_gateway.testing_specs:HANDLER_MODULE")


async def test_structured_handler_returning_free_text_is_a_config_error() -> None:
    spec = dataclasses.replace(
        ECHO_SPEC, mock_handler="mde_llm_gateway.testing_specs:wrong_type_handle"
    )
    with pytest.raises(ConfigError, match="FreeTextResult"):
        await MockProvider().complete(_request(spec=spec))


async def test_free_text_handler_returning_a_model_is_a_config_error() -> None:
    spec = dataclasses.replace(
        SEARCH_SPEC, mock_handler="mde_llm_gateway.testing_specs:echo_handle"
    )
    with pytest.raises(ConfigError, match="must return FreeTextResult"):
        await MockProvider().complete(_request("T4.search", spec=spec))


async def test_handler_output_that_does_not_match_the_model_is_a_config_error() -> None:
    spec = dataclasses.replace(
        ECHO_SPEC, mock_handler="mde_llm_gateway.testing_specs:bad_output_handle"
    )
    with pytest.raises(ConfigError, match="does not match EchoOut"):
        await MockProvider().complete(_request(spec=spec))


async def test_handler_may_return_a_dict_or_be_async() -> None:
    spec = dataclasses.replace(ECHO_SPEC, mock_handler="tests_support_handlers:dict_handle")
    import sys
    import types

    mod = types.ModuleType("tests_support_handlers")

    def dict_handle(_input: EchoIn) -> dict[str, object]:
        return {"answer": "from dict", "confidence": 0.1}

    async def async_handle(_input: EchoIn) -> EchoOut:
        return EchoOut(answer="from async", confidence=0.2)

    mod.dict_handle = dict_handle  # type: ignore[attr-defined]
    mod.async_handle = async_handle  # type: ignore[attr-defined]
    sys.modules["tests_support_handlers"] = mod
    try:
        assert "from dict" in (await MockProvider().complete(_request(spec=spec))).text
        spec2 = dataclasses.replace(ECHO_SPEC, mock_handler="tests_support_handlers:async_handle")
        assert "from async" in (await MockProvider().complete(_request(spec=spec2))).text
    finally:
        del sys.modules["tests_support_handlers"]


async def test_the_whole_pipeline_runs_offline_on_the_mock_provider() -> None:
    gw = build_test_gateway(MockProvider())
    result = await gw.run(ECHO_SPEC, EchoIn(question="hi"), make_ctx())
    assert result.mode == "mock" and result.usage.cost_micros == 0


# ---------------------------------------------------------------------------- ScriptedProvider
async def test_queue_is_fifo_per_call_id_and_accepts_models_dicts_and_text() -> None:
    p = ScriptedProvider()
    p.queue("T1.echo", EchoOut(answer="aaa", confidence=0.1), {"k": 1}, "raw")
    texts = [(await p.complete(_request())).text for _ in range(3)]
    assert texts[0] == EchoOut(answer="aaa", confidence=0.1).model_dump_json()
    assert texts[1] == '{"k": 1}' and texts[2] == "raw"
    assert p.pending == 0 and len(p.calls_for("T1.echo")) == 3 and p.calls_for("other") == []


async def test_defaults_and_wildcard_default() -> None:
    p = ScriptedProvider().set_default("T1.echo", "specific").set_default("*", "wild")
    assert (await p.complete(_request())).text == "specific"
    assert (await p.complete(_request("T4.search"))).text == "wild"
    p.queue("T1.echo", "queued")
    assert (await p.complete(_request())).text == "queued"  # the queue beats the default
    assert (await p.complete(_request())).text == "specific"


async def test_exhausted_script_raises() -> None:
    with pytest.raises(ScriptExhausted):
        await ScriptedProvider().complete(_request())


async def test_errors_are_raised_and_callables_are_called_with_the_request() -> None:
    p = ScriptedProvider()
    p.queue("T1.echo", ProviderCallError("429", status=429, retryable=True))
    with pytest.raises(ProviderCallError):
        await p.complete(_request())
    p.queue("T1.echo", lambda req: f"saw {req.model}")
    assert (await p.complete(_request())).text == "saw gpt-6-luna"
    p.queue("T1.echo", lambda _req: lambda _req2: "nested")
    assert (await p.complete(_request())).text == "nested"


async def test_scripted_response_controls_usage_citations_status_and_delay() -> None:
    clock = FakeClock()
    p = ScriptedProvider(clock=clock)
    usage = Usage(input_tokens=7, output_tokens=3, reasoning_tokens=2)
    p.queue(
        "T1.echo",
        ScriptedResponse(
            output="x",
            usage=usage,
            status="incomplete",
            incomplete_reason="max_output_tokens",
            search_calls=2,
            delay=2.5,
        ),
        ScriptedResponse(output="y", usage=NO_USAGE),
    )
    first = await p.complete(_request())
    assert first.usage == usage and first.status == "incomplete" and first.search_calls == 2
    assert first.incomplete_reason == "max_output_tokens"
    assert clock.sleeps == [2.5]
    assert (await p.complete(_request())).usage is None  # a response that carries no usage


async def test_scripted_provider_flags() -> None:
    assert ScriptedProvider().billable is False and ScriptedProvider().mode == "mock"
    live = ScriptedProvider(mode="live", billable=True)
    assert live.billable and live.mode == "live"
