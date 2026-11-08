"""In-process client, registry and wire format, tested inside the package (the full HTTP contract
suite lives in apps/llm-gateway-api/tests)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from mde_contracts.gateway import CallResult, Usage
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    GatewayError,
    NotFound,
    ProviderError,
    StructuredOutputError,
)
from mde_llm_gateway.client import InProcessGatewayClient
from mde_llm_gateway.errors import ERROR_STATUS, GatewayAuthError
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_llm_gateway.providers import ScriptedProvider
from mde_llm_gateway.registry import StaticRegistry
from mde_llm_gateway.testing_specs import (
    ALL_SPECS,
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    build_test_gateway,
    make_ctx,
)
from mde_llm_gateway.wire import (
    ErrorBody,
    ErrorEnvelope,
    RunContext,
    context_from_wire,
    context_to_wire,
    error_from_wire,
    error_to_wire,
)

GOOD = EchoOut(answer="hello there", confidence=0.5)


async def test_in_process_client_runs_and_reports_budget(clock: FakeClock) -> None:
    provider = ScriptedProvider(mode="live", billable=True, clock=clock).queue("T1.echo", GOOD)
    client = InProcessGatewayClient(build_test_gateway(provider, clock=clock))
    result = await client.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert result.output == GOOD
    status = await client.budget_status()
    assert status.spent_micros == result.usage.cost_micros > 0


def test_static_registry_lookup_and_errors() -> None:
    registry = StaticRegistry(ALL_SPECS)
    assert registry.get("T1.echo") is ECHO_SPEC
    assert registry.get("T4.search", 2) is SEARCH_SPEC
    assert {s.id for s in registry.catalog()} == {s.id for s in ALL_SPECS}
    with pytest.raises(NotFound, match="unknown call id"):
        registry.get("nope")
    with pytest.raises(NotFound, match="v9"):
        registry.get("T1.echo", 9)


def test_context_survives_the_wire_without_the_cancel_token() -> None:
    deadline = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)
    ctx = make_ctx(
        scope="session:s", extra_scopes=("research:r",), purpose="p", session_id="ses_1",
        tenant_id="tnt_1", request_id="req_9", deadline=deadline, route_override="deep",
    )  # fmt: skip
    wire = context_to_wire(ctx)
    assert RunContext.model_validate_json(wire.model_dump_json()) == wire
    token = CancellationToken()
    back = context_from_wire(wire, token)
    assert back.scopes == ("session:s", "research:r") and back.deadline == deadline
    assert back.cancel is token and back.route_override == "deep"
    assert context_from_wire(wire).cancel.cancelled is False


def test_provider_error_fields_round_trip() -> None:
    exc = ProviderError("busy", status=429, retryable=True, retry_after=1.5, details={"x": 1})
    status, envelope = error_to_wire(exc)
    assert status == 502 and envelope.error.details["provider_status"] == 429
    back = error_from_wire(status, envelope)
    assert isinstance(back, ProviderError)
    assert (back.status, back.retryable, back.retry_after) == (429, True, 1.5)
    assert back.details == {"x": 1}


def test_non_mde_exceptions_leak_nothing() -> None:
    status, envelope = error_to_wire(RuntimeError("internal secret detail"))
    assert status == 500 and "secret" not in envelope.model_dump_json()


def test_details_are_made_json_safe() -> None:
    exc = BudgetExceeded("over", details={"when": datetime(2026, 11, 8, tzinfo=UTC), "n": 1})
    _, envelope = error_to_wire(exc)
    assert json.loads(envelope.model_dump_json())["error"]["details"]["n"] == 1


def test_every_status_in_the_table_is_an_http_error() -> None:
    assert all(400 <= s <= 599 for s in ERROR_STATUS.values())


def _success_body(output: object | None) -> dict[str, object]:
    result = CallResult(
        call_id="call_1", spec_id="T1.echo", spec_version=1, model="gpt-6-luna", mode="live",
        output=None, usage=Usage(input_tokens=5, cost_micros=7), request_id="req_1",
    )  # fmt: skip
    return {**json.loads(result.model_dump_json()), "output": output}


def _client(handler: httpx.MockTransport) -> HttpGatewayClient:
    return HttpGatewayClient("http://gw.test", "tok", transport=handler)


async def test_http_client_parses_a_typed_result_and_sends_the_token() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_success_body(GOOD.model_dump(mode="json")))

    result = await _client(httpx.MockTransport(respond)).run(
        ECHO_SPEC, EchoIn(question="q"), make_ctx()
    )
    assert result.output == GOOD and result.usage.cost_micros == 7
    assert seen[0].headers["authorization"] == "Bearer tok" and seen[0].url.path == "/v1/runs"
    body = json.loads(seen[0].content)
    assert body["call_id"] == "T1.echo" and body["version"] == 1
    assert body["input"] == {"question": "q", "style": "short"}


async def test_http_client_free_text_has_no_output() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=_success_body(None))))
    result = await client.run(SEARCH_SPEC, EchoIn(question="q"), make_ctx())  # type: ignore[arg-type]
    assert result.output is None


async def test_http_client_error_envelopes_become_the_same_exceptions() -> None:
    envelope = ErrorEnvelope(
        error=ErrorBody(code="structured_output_error", message="bad", details={"repaired": True})
    )
    client = _client(
        httpx.MockTransport(lambda _r: httpx.Response(502, json=envelope.model_dump()))
    )
    with pytest.raises(StructuredOutputError) as exc:
        await client.run(ECHO_SPEC, EchoIn(question="q"), make_ctx())
    assert exc.value.details == {"repaired": True}


async def test_http_client_handles_garbage_and_auth_and_transport_errors() -> None:
    def status(code: int, text: str = "x") -> HttpGatewayClient:
        return _client(httpx.MockTransport(lambda _r: httpx.Response(code, text=text)))

    with pytest.raises(GatewayAuthError):
        await status(401).budget_status()
    with pytest.raises(GatewayError, match="HTTP 500"):
        await status(500).budget_status()
    with pytest.raises(GatewayError, match="not JSON"):
        await status(200, "nope").budget_status()
    with pytest.raises(GatewayError, match="unreadable result"):
        await status(200, '{"call_id": 1}').run(ECHO_SPEC, EchoIn(question="q"), make_ctx())

    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    def slow(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(GatewayError, match="unreachable"):
        await _client(httpx.MockTransport(refuse)).budget_status()
    with pytest.raises(GatewayError, match="timed out"):
        await _client(httpx.MockTransport(slow)).budget_status()


async def test_http_client_stops_at_once_when_already_cancelled() -> None:
    token = CancellationToken()
    token.cancel()
    client = _client(httpx.MockTransport(lambda _r: pytest.fail("must not send")))
    with pytest.raises(CallCancelled):
        await client.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(cancel=token))
    await client.aclose()
