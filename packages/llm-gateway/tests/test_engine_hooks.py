"""The three small signals an engine can read from the gateway, all vendor-neutral and optional:

* ``GatewayConfig.on_provider_error``: every failed provider attempt, a 429 that the retries absorb included;
* ``details["truncated"]`` on the ``StructuredOutputError`` of an answer that was cut off at the output limit;
* ``GatewayConfig.json_candidates``: a lenient reader for text that is not JSON as it stands (no repair call paid).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from mde_core.clock import FakeClock
from mde_core.errors import ProviderError, StructuredOutputError
from mde_llm_gateway.gateway import GatewayConfig
from mde_llm_gateway.providers import ProviderCallError, ScriptedProvider, ScriptedResponse
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    EchoIn,
    EchoOut,
    build_test_gateway,
    make_ctx,
)

GOOD = EchoOut(answer="hello there", confidence=0.5)
GOOD_JSON = GOOD.model_dump_json()
BAD = {"answer": "x", "confidence": 0.5}
Q = EchoIn(question="what is 2+2?")
CUT = ScriptedResponse(
    output='{"answer": "he', status="incomplete", incomplete_reason="max_output_tokens"
)


def _provider(clock: FakeClock) -> ScriptedProvider:
    return ScriptedProvider(mode="mock", billable=False, clock=clock)


def _retrying(**kw: object) -> GatewayConfig:
    retry = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=8.0, jitter=0.0)
    return GatewayConfig(retry=retry, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- on_provider_error
async def test_the_observer_sees_every_failed_attempt_even_when_the_retries_absorb_them(
    clock: FakeClock,
) -> None:
    seen: list[tuple[int | None, bool]] = []
    provider = _provider(clock).queue(
        "T1.echo",
        ProviderCallError("slow down", status=429, retryable=True),
        ProviderCallError("slow down", status=429, retryable=True),
        GOOD,
    )
    gw = build_test_gateway(
        provider,
        clock=clock,
        config=_retrying(on_provider_error=lambda e: seen.append((e.status, e.retryable))),
    )
    result = await gw.run(ECHO_SPEC, Q, make_ctx())
    assert result.status == "ok"  # the caller got an answer ...
    assert seen == [(429, True), (429, True)]  # ... and still learned about the pressure


async def test_the_observer_also_sees_the_error_that_ends_the_call(clock: FakeClock) -> None:
    seen: list[int | None] = []
    provider = _provider(clock).set_default(
        "T1.echo", ProviderCallError("down", status=503, retryable=True)
    )
    gw = build_test_gateway(
        provider, clock=clock, config=_retrying(on_provider_error=lambda e: seen.append(e.status))
    )
    with pytest.raises(ProviderError):
        await gw.run(ECHO_SPEC, Q, make_ctx())
    assert seen == [503] * 4  # one per attempt, the last one included


async def test_an_observer_that_raises_never_breaks_the_call(clock: FakeClock) -> None:
    def boom(_: ProviderError) -> None:
        raise RuntimeError("observer bug")

    provider = _provider(clock).queue(
        "T1.echo", ProviderCallError("slow down", status=429, retryable=True), GOOD
    )
    gw = build_test_gateway(provider, clock=clock, config=_retrying(on_provider_error=boom))
    assert (await gw.run(ECHO_SPEC, Q, make_ctx())).status == "ok"


async def test_the_observer_gets_a_scrubbed_copy(clock: FakeClock) -> None:
    key = "sk-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # secret-scan: allow
    messages: list[str] = []
    provider = _provider(clock).queue(
        "T1.echo", ProviderCallError(f"bad key {key}", status=401, retryable=False)
    )
    gw = build_test_gateway(
        provider, clock=clock, config=_retrying(on_provider_error=lambda e: messages.append(str(e)))
    )
    with pytest.raises(ProviderError):
        await gw.run(ECHO_SPEC, Q, make_ctx())
    assert messages and key not in messages[0]


# ------------------------------------------------------------------------------------------- details["truncated"]
async def test_an_answer_cut_off_twice_says_so_with_a_typed_flag(clock: FakeClock) -> None:
    provider = _provider(clock).queue("T1.echo", CUT, CUT)
    gw = build_test_gateway(provider, clock=clock)
    with pytest.raises(StructuredOutputError) as exc:
        await gw.run(ECHO_SPEC, Q, make_ctx())
    assert exc.value.details["truncated"] is True


async def test_a_cut_off_that_is_still_cut_after_the_repair_is_truncated_too(
    clock: FakeClock,
) -> None:
    spec = dataclasses.replace(ECHO_SPEC, reasoning_effort="high")  # a repair at "low" is allowed
    provider = _provider(clock).queue("T1.echo", CUT, CUT, CUT)
    gw = build_test_gateway(provider, clock=clock)
    with pytest.raises(StructuredOutputError) as exc:
        await gw.run(spec, Q, make_ctx())
    assert exc.value.details["repaired"] is True and exc.value.details["truncated"] is True


async def test_other_failures_are_not_flagged_as_truncation(clock: FakeClock) -> None:
    for first, second in (
        (BAD, BAD),  # invalid after the repair
        (ScriptedResponse(output="", refusal="no"), None),  # a refusal
        (
            ScriptedResponse(output="", status="incomplete", incomplete_reason="content_filter"),
            None,
        ),
    ):
        provider = _provider(clock).queue("T1.echo", first, *([second] if second else []))
        gw = build_test_gateway(provider, clock=clock)
        with pytest.raises(StructuredOutputError) as exc:
            await gw.run(ECHO_SPEC, Q, make_ctx())
        assert exc.value.details["truncated"] is False


async def test_a_repair_that_completes_but_is_invalid_is_not_truncation(clock: FakeClock) -> None:
    spec = dataclasses.replace(ECHO_SPEC, reasoning_effort="high")
    provider = _provider(clock).queue("T1.echo", CUT, CUT, BAD)
    gw = build_test_gateway(provider, clock=clock)
    with pytest.raises(StructuredOutputError) as exc:
        await gw.run(spec, Q, make_ctx())
    assert exc.value.details["truncated"] is False


# ------------------------------------------------------------------------------------------- json_candidates
def _candidates(text: str) -> list[str]:
    """A tiny reader for the tests: every top-level object that parses, in order (the Spec agent has a fuller one)."""
    decoder = json.JSONDecoder()
    out: list[str] = []
    i = text.find("{")
    while i != -1:
        try:
            _, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        out.append(text[i:end])
        i = text.find("{", end)
    return out


async def test_a_wrapped_valid_answer_is_read_without_a_repair_call(clock: FakeClock) -> None:
    provider = _provider(clock).queue("T1.echo", "Here you go:\n```json\n" + GOOD_JSON + "\n```")
    gw = build_test_gateway(
        provider, clock=clock, config=GatewayConfig(json_candidates=_candidates)
    )
    result = await gw.run(ECHO_SPEC, Q, make_ctx())
    assert (result.status, result.repaired, result.output) == ("ok", False, GOOD)
    assert len(provider.calls) == 1


async def test_the_first_candidate_that_validates_wins_a_decoy_is_skipped(clock: FakeClock) -> None:
    text = 'Thinking: {"a": 1} so the answer is ' + GOOD_JSON
    provider = _provider(clock).queue("T1.echo", text)
    gw = build_test_gateway(
        provider, clock=clock, config=GatewayConfig(json_candidates=_candidates)
    )
    assert (await gw.run(ECHO_SPEC, Q, make_ctx())).output == GOOD
    assert len(provider.calls) == 1


async def test_a_clean_answer_never_reaches_the_reader(clock: FakeClock) -> None:
    calls: list[str] = []

    def spy(text: str) -> list[str]:
        calls.append(text)
        return _candidates(text)

    provider = _provider(clock).queue("T1.echo", GOOD)
    gw = build_test_gateway(provider, clock=clock, config=GatewayConfig(json_candidates=spy))
    await gw.run(ECHO_SPEC, Q, make_ctx())
    assert calls == []  # the strict path is still the first thing tried


async def test_candidates_that_do_not_validate_go_to_the_repair_with_their_problems(
    clock: FakeClock,
) -> None:
    provider = _provider(clock).queue("T1.echo", "Answer: " + json.dumps(BAD), GOOD)
    gw = build_test_gateway(
        provider, clock=clock, config=GatewayConfig(json_candidates=_candidates)
    )
    result = await gw.run(ECHO_SPEC, Q, make_ctx())
    assert result.repaired and [c.kind for c in provider.calls] == ["main", "repair"]
    assert "answer: String should have at least 3 characters" in provider.calls[1].user


async def test_a_reader_that_raises_or_finds_nothing_falls_back_to_the_normal_repair(
    clock: FakeClock,
) -> None:
    def boom(_: str) -> list[str]:
        raise RuntimeError("reader bug")

    for reader in (boom, lambda _t: []):
        provider = _provider(clock).queue("T1.echo", "no json here", GOOD)
        gw = build_test_gateway(provider, clock=clock, config=GatewayConfig(json_candidates=reader))
        result = await gw.run(ECHO_SPEC, Q, make_ctx())
        assert result.repaired and "not valid JSON" in provider.calls[1].user


async def test_without_a_reader_the_strict_behaviour_is_unchanged(clock: FakeClock) -> None:
    provider = _provider(clock).queue("T1.echo", "Here:\n" + GOOD_JSON, GOOD)
    gw = build_test_gateway(provider, clock=clock)
    assert (await gw.run(ECHO_SPEC, Q, make_ctx())).repaired
