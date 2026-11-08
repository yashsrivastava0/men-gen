"""Logs never carry the API key or prompt text (brief 7.11, 13 rule 1)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
import structlog

from mde_contracts.gateway import Usage
from mde_core.clock import FakeClock
from mde_core.errors import ProviderError
from mde_core.logging import configure_logging
from mde_llm_gateway.gateway import GatewayConfig
from mde_llm_gateway.providers import ProviderCallError, ScriptedProvider, ScriptedResponse
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    EchoIn,
    EchoOut,
    build_test_gateway,
    fake_api_key,
    make_ctx,
    make_settings,
)

PROMPT_MARKER = "very-private-question-7f3a"
GOOD = EchoOut(answer="hello there", confidence=0.5)


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("DEBUG", json=True, stream=stream)
    yield stream
    structlog.reset_defaults()


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]


async def test_key_and_prompt_text_never_reach_the_logs(
    log_stream: io.StringIO, clock: FakeClock
) -> None:
    key = fake_api_key()
    settings = make_settings(openai_api_key=key)
    provider = ScriptedProvider(mode="live", billable=True, clock=clock)
    provider.queue(
        "T1.echo",
        ProviderCallError(f"upstream rejected key {key} for you", status=503, retryable=True),
        ScriptedResponse(output=GOOD, usage=Usage(input_tokens=50, output_tokens=10)),
        ProviderCallError(f"fatal: bearer {key}", status=400, retryable=False),
    )
    config = GatewayConfig(retry=RetryPolicy(jitter=0.0, base_delay=1.0, max_attempts=3))
    gw = build_test_gateway(provider, clock=clock, settings=settings, config=config)
    question = f"{PROMPT_MARKER} and my key is {key}"
    await gw.run(ECHO_SPEC, EchoIn(question=question), make_ctx())
    with pytest.raises(ProviderError) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question=question), make_ctx())

    text = log_stream.getvalue()
    assert key not in text and PROMPT_MARKER not in text
    assert key not in str(exc.value) and "[REDACTED]" in str(exc.value)
    events = _events(log_stream)
    calls = [e for e in events if e["event"] == "llm_call"]
    assert [c["status"] for c in calls] == ["ok", "failed"]
    ok = calls[0]
    for field in (
        "request_id", "session_id", "call_id", "spec_id", "model", "mode", "input_tokens",
        "output_tokens", "cost_micros", "latency_ms", "status", "repaired", "cache_hit",
        "cassette_key", "prompt_hash",
    ):  # fmt: skip
        assert field in ok, field
    assert len(str(ok["prompt_hash"])) == 64 and "prompt" not in ok
    assert any(e["event"] == "llm_retry" for e in events)


async def test_log_prompts_is_opt_in_and_redacted(
    log_stream: io.StringIO, clock: FakeClock
) -> None:
    key = fake_api_key()
    settings = make_settings(openai_api_key=key, log_prompts=True)
    provider = ScriptedProvider(clock=clock).queue("T1.echo", GOOD)
    gw = build_test_gateway(provider, clock=clock, settings=settings)
    await gw.run(ECHO_SPEC, EchoIn(question=f"{PROMPT_MARKER} key {key}"), make_ctx())
    text = log_stream.getvalue()
    assert PROMPT_MARKER in text  # opted in
    assert key not in text and "[REDACTED]" in text  # but still redacted
    logged = next(e for e in _events(log_stream) if e["event"] == "llm_call")
    assert len(str(logged["prompt"])) <= 4000


async def test_provider_error_messages_are_scrubbed_even_without_logging(clock: FakeClock) -> None:
    key = fake_api_key()
    provider = ScriptedProvider(clock=clock).queue(
        "T1.echo", ProviderCallError(f"bad key {key}", status=401, retryable=False)
    )
    gw = build_test_gateway(provider, clock=clock, settings=make_settings(openai_api_key=key))
    with pytest.raises(ProviderError) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert key not in str(exc.value) and exc.value.status == 401
