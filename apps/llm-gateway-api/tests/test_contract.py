"""The gateway contract suite: ONE set of behaviours run against the in-process client AND the HTTP
client (through the real FastAPI app over ASGI). Results and exceptions must be identical (brief 7.12).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mde_contracts.gateway import Citation, Usage
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    ProviderError,
    StructuredOutputError,
    UnknownModelPrice,
    UntrustedWithTools,
    ValidationFailed,
)
from mde_llm_gateway.errors import DeadlineExceeded
from mde_llm_gateway.gateway import GatewayConfig
from mde_llm_gateway.providers import ProviderCallError, ScriptedResponse
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.testing_specs import (
    CACHEABLE_SPEC,
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    SearchIn,
    make_ctx,
    sneaky_untrusted_tools_spec,
)
from mde_llm_gateway_api.testing import Harness, HarnessFactory, build_harness

GOOD = EchoOut(answer="hello there", confidence=0.5)
BAD = {"answer": "x", "confidence": 0.5}
Q = EchoIn(question="what is 2+2?")
NO_RETRY = GatewayConfig(retry=RetryPolicy(max_attempts=1))


def _good(h: Harness, **usage: int) -> None:
    h.provider.queue(
        "T1.echo",
        ScriptedResponse(output=GOOD, usage=Usage(input_tokens=1000, output_tokens=200, **usage)),
    )


# ---------------------------------------------------------------------------- success
async def test_success(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    _good(h)
    result = await h.client.run(ECHO_SPEC, Q, make_ctx(request_id="req_42"))
    assert isinstance(result.output, EchoOut) and result.output == GOOD
    assert (result.status, result.mode, result.repaired, result.cache_hit) == (
        "ok", "live", False, False,
    )  # fmt: skip
    assert (result.spec_id, result.spec_version, result.model) == ("T1.echo", 1, "gpt-6-luna")
    assert result.call_id.startswith("call_") and result.request_id == "req_42"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1000, 200)
    assert result.usage.cost_micros == 100 + 100  # 1000 x 0.10 + 200 x 0.50, in micro-dollars
    assert result.text is None and result.citations == [] and result.search_calls == 0


async def test_free_text_web_search_result(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    cites = [Citation(url="https://a.example/x", title="X", start=0, end=4)]
    h.provider.queue(
        "T4.search",
        ScriptedResponse(
            output="answer text", citations=cites, search_calls=2,
            usage=Usage(input_tokens=2000, output_tokens=100),
        ),
    )  # fmt: skip
    result = await h.client.run(SEARCH_SPEC, SearchIn(query="q"), make_ctx())
    assert result.output is None and result.text == "answer text"
    assert result.citations == cites and result.search_calls == 2
    assert result.usage.tool_calls == 2
    assert result.usage.cost_micros == 200 + 50 + 20_000  # tokens plus 2 x the 0.01 USD search fee


async def test_repair_path(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    h.provider.queue("T1.echo", BAD, GOOD)
    result = await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert (result.status, result.repaired) == ("repaired", True) and result.output == GOOD
    assert [c.kind for c in h.provider.calls] == ["main", "repair"]


async def test_cache_hit(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    h.provider.queue("T2.cacheable", GOOD)
    first = await h.client.run(CACHEABLE_SPEC, Q, make_ctx())
    second = await h.client.run(CACHEABLE_SPEC, Q, make_ctx())
    assert not first.cache_hit and second.cache_hit and second.status == "cache_hit"
    assert second.usage.cost_micros == 0 and second.output == first.output
    assert len(h.provider.calls) == 1


# ---------------------------------------------------------------------------- failures map to the same exceptions
async def test_fallback_path_structured_output_error(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    h.provider.queue("T1.echo", BAD, BAD)
    with pytest.raises(StructuredOutputError) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert exc.value.code == "structured_output_error"
    assert exc.value.details["repaired"] is True and exc.value.details["call_id"] == "T1.echo"
    assert len(h.provider.calls) == 2  # one repair, never more


async def test_budget_exceeded(harness_factory: HarnessFactory) -> None:
    h = harness_factory(cap_usd="0.0001")
    _good(h)
    with pytest.raises(BudgetExceeded) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert exc.value.details["scope"] == "global"
    assert h.provider.calls == [] and h.ledger.lines == []  # refused before any reservation


async def test_scoped_budget_exceeded_names_the_scope(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    rsv = h.gateway.guard.reserve(["session:ses_1"], 499_900, model="gpt-6-luna", mode="live")
    h.gateway.guard.commit_cost(rsv, 499_900)  # 100 micros left of the session's 0.50 USD
    _good(h)
    with pytest.raises(BudgetExceeded) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx(scope="session:ses_1"))
    assert exc.value.details["scope"] == "session:ses_1"


async def test_untrusted_plus_tools_is_rejected(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    with pytest.raises(UntrustedWithTools):
        await h.client.run(sneaky_untrusted_tools_spec(), SearchIn(query="x"), make_ctx())  # type: ignore[arg-type]
    assert h.provider.calls == []


async def test_invalid_input_is_a_validation_error(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    broken = EchoIn.model_construct(question=123, style="short")  # skips validators on purpose
    with (
        pytest.warns(UserWarning, match="serializ"),  # pydantic warns about the bad value
        pytest.raises(ValidationFailed),
    ):
        await h.client.run(ECHO_SPEC, broken, make_ctx())
    assert h.provider.calls == []


async def test_unknown_model_price_is_refused(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    h.gateway.settings.model_routes["fast"] = "gpt-9-nova"
    with pytest.raises(UnknownModelPrice):
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert h.provider.calls == []


async def test_non_retryable_provider_error(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    h.provider.queue("T1.echo", ProviderCallError("bad request", status=400, retryable=False))
    with pytest.raises(ProviderError) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert (exc.value.status, exc.value.retryable) == (400, False)


async def test_retryable_provider_error_after_retries_keeps_status_and_retry_after(
    harness_factory: HarnessFactory,
) -> None:
    h = harness_factory(config=GatewayConfig(retry=RetryPolicy(max_attempts=2, jitter=0.0)))
    h.provider.set_default(
        "T1.echo", ProviderCallError("busy", status=429, retryable=True, retry_after=2.5)
    )
    with pytest.raises(ProviderError) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert (exc.value.status, exc.value.retryable, exc.value.retry_after) == (429, True, 2.5)
    assert len(h.provider.calls) == 2


async def test_circuit_open(harness_factory: HarnessFactory) -> None:
    h = harness_factory(config=GatewayConfig(retry=RetryPolicy(max_attempts=1), breaker_failures=1))
    h.provider.set_default("T1.echo", ProviderCallError("down", status=503, retryable=True))
    with pytest.raises(ProviderError):
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    with pytest.raises(CircuitOpen) as exc:
        await h.client.run(ECHO_SPEC, Q, make_ctx())
    assert exc.value.details["state"] == "open" and len(h.provider.calls) == 1


async def test_deadline_exceeded(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    past = h.clock.now() - timedelta(seconds=1)
    with pytest.raises(DeadlineExceeded):
        await h.client.run(ECHO_SPEC, Q, make_ctx(deadline=past))
    assert h.provider.calls == []


async def test_cancelled_call_never_reaches_the_provider(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    token = CancellationToken()
    token.cancel("stop")
    with pytest.raises(CallCancelled):
        await h.client.run(ECHO_SPEC, Q, make_ctx(cancel=token))
    assert h.provider.calls == []


# ---------------------------------------------------------------------------- budget status and context
async def test_budget_status_reflects_spend(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    _good(h)
    await h.client.run(ECHO_SPEC, Q, make_ctx(scope="session:ses_7"))
    status = await h.client.budget_status()
    assert status.cap_micros == 6_000_000 and status.effective_cap_micros == 5_700_000
    assert (status.spent_micros, status.reserved_micros, status.live_calls) == (200, 0, 1)
    assert status.by_scope == {"session:ses_7": 200} and status.tripwire == "ok"
    assert status == h.gateway.budget_status()


async def test_context_reaches_the_gateway(harness_factory: HarnessFactory) -> None:
    h = harness_factory()
    _good(h)
    ctx = make_ctx(
        scope="session:ses_3", extra_scopes=("research:run_9",), purpose="must-run:probe",
        session_id="ses_3", tenant_id="tnt_1", request_id="req_ctx",
        route_override="deep", deadline=h.clock.now() + timedelta(seconds=90),
    )  # fmt: skip
    await h.client.run(ECHO_SPEC, Q, ctx)
    reserve = h.ledger.events()[0]
    assert reserve.scopes == ["session:ses_3", "research:run_9"]
    record = h.sink.records[0]
    assert (record.session_id, record.tenant_id, record.request_id) == ("ses_3", "tnt_1", "req_ctx")
    assert record.scope == "session:ses_3"
    assert h.provider.calls[0].timeout_s == pytest.approx(90.0)  # min(deep 120s, deadline 90s)


# ---------------------------------------------------------------------------- identical results
async def test_both_clients_return_identical_results(clock: FakeClock) -> None:
    """Run one scripted scenario against each client: every field except ids and timing matches."""

    async def scenario(kind: str) -> list[dict[str, object]]:
        h = build_harness(kind, FakeClock())
        h.provider.queue(
            "T1.echo",
            BAD,
            ScriptedResponse(output=GOOD, usage=Usage(input_tokens=77, output_tokens=9)),
        )
        h.provider.queue("T4.search", ScriptedResponse(output="found", citations=[Citation(url="https://z.example")], search_calls=1))  # fmt: skip
        out: list[dict[str, object]] = []
        for spec, inp in ((ECHO_SPEC, Q), (SEARCH_SPEC, SearchIn(query="s"))):
            result = await h.client.run(spec, inp, make_ctx())  # type: ignore[arg-type]
            out.append(result.model_dump(mode="json", exclude={"call_id", "latency_ms"}))
        return out

    assert await scenario("inprocess") == await scenario("http")
