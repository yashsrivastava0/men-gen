"""HTTP surface of the gateway API: auth, routes, catalog, error envelope, client transport errors."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from mde_contracts.gateway import Usage
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import CallCancelled, GatewayError
from mde_llm_gateway.errors import ERROR_CLASSES, ERROR_STATUS, GatewayAuthError
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_llm_gateway.providers import ScriptedResponse
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    EXTRACT_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    fake_api_key,
    make_ctx,
    make_settings,
    make_test_registry,
)
from mde_llm_gateway.wire import ErrorBody, ErrorEnvelope, error_from_wire, error_to_wire
from mde_llm_gateway_api.app import create_app
from mde_llm_gateway_api.testing import BASE_URL, TOKEN, Harness, build_harness

GOOD = EchoOut(answer="hello there", confidence=0.5)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def http(app: FastAPI, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


def make_harness(clock: FakeClock | None = None) -> Harness:
    return build_harness("http", clock or FakeClock())


def app_of(h: Harness) -> FastAPI:
    client = h.client
    assert isinstance(client, HttpGatewayClient)
    transport = client._client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return transport.app  # type: ignore[return-value]


# ---------------------------------------------------------------------------- auth
async def test_every_v1_route_needs_the_bearer_token() -> None:
    app = app_of(make_harness())
    async with http(app) as client:
        for method, path in (
            ("GET", "/v1/calls"),
            ("GET", "/v1/budget"),
            ("GET", "/v1/usage"),
            ("POST", "/v1/runs"),
        ):
            for headers in (
                {},
                {"Authorization": "Bearer wrong"},
                {"Authorization": "Basic " + TOKEN},
            ):
                resp = await client.request(
                    method, path, headers=headers, json={} if method == "POST" else None
                )
                assert resp.status_code == 401, (method, path, headers)
                assert resp.json()["error"]["code"] == "unauthorized"
                assert resp.headers["www-authenticate"] == "Bearer"


async def test_health_and_ready_need_no_token_and_leak_no_secrets() -> None:
    app = app_of(make_harness())
    async with http(app) as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        ready = (await client.get("/readyz")).json()
    assert ready == {"status": "ready"}  # nothing about mode, provider, billing or the budget
    assert TOKEN not in str(ready)


async def test_without_a_configured_token_the_api_refuses_everything() -> None:
    gateway = make_harness().gateway
    app = create_app(make_settings(), make_test_registry(), gateway=gateway)  # no token
    async with http(app) as client:
        resp = await client.get("/v1/budget", headers=AUTH)
        assert resp.status_code == 500 and resp.json()["error"]["code"] == "config_error"
        assert (await client.get("/healthz")).status_code == 200


async def test_readyz_reports_not_ready_when_the_ledger_breaks() -> None:
    h = make_harness()
    app = app_of(h)

    def boom() -> None:
        raise RuntimeError("ledger gone")

    h.gateway.guard.status = boom  # type: ignore[method-assign,assignment]
    async with http(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503 and resp.json() == {"status": "not_ready"}


# ---------------------------------------------------------------------------- catalog, budget, usage
async def test_catalog_lists_specs_with_io_schemas() -> None:
    app = app_of(make_harness())
    async with http(app) as client:
        body = (await client.get("/v1/calls", headers=AUTH)).json()
    by_id = {e["id"]: e for e in body}
    assert {"T1.echo", "T4.search", "T5.extract"} <= set(by_id)
    echo = by_id["T1.echo"]
    assert (echo["version"], echo["route"], echo["reasoning_effort"]) == (1, "fast", "low")
    assert echo["max_output_tokens"] == 300 and echo["fallback"] == "echo_fallback"
    assert echo["input_schema"]["properties"]["question"]["type"] == "string"
    assert echo["output_schema"]["properties"]["confidence"]["maximum"] == 1.0
    search = by_id["T4.search"]
    assert search["output_schema"] is None and search["tools"][0]["kind"] == "web_search"
    assert search["tools"][0]["max_tool_calls"] == 2
    assert by_id["T5.extract"]["untrusted_inputs"] == ["page", "notes"]
    assert by_id["T2.cacheable"]["cacheable"] is True and by_id["T3.norepair"]["repair"] is False
    assert (EXTRACT_SPEC.id, SEARCH_SPEC.id) == ("T5.extract", "T4.search")


async def test_usage_and_budget_routes() -> None:
    h = make_harness()
    h.provider.queue(
        "T1.echo", ScriptedResponse(output=GOOD, usage=Usage(input_tokens=1000, output_tokens=200))
    )
    await h.client.run(
        ECHO_SPEC,
        EchoIn(question="q"),
        make_ctx(scope="session:ses_5", extra_scopes=("research:r5",)),
    )
    async with http(app_of(h)) as client:
        budget = (await client.get("/v1/budget", headers=AUTH)).json()
        total = (await client.get("/v1/usage", headers=AUTH)).json()
        session = (
            await client.get("/v1/usage", params={"scope": "session:ses_5"}, headers=AUTH)
        ).json()
        research = (
            await client.get("/v1/usage", params={"scope": "research:r5"}, headers=AUTH)
        ).json()
        other = (await client.get("/v1/usage", params={"scope": "adhoc:none"}, headers=AUTH)).json()
    assert budget["spent_micros"] == 200 and budget["effective_cap_micros"] == 5_700_000
    assert total["spent_micros"] == 200 and total["by_scope"] == {
        "session:ses_5": 200,
        "research:r5": 200,
    }
    assert total["cap_micros"] == 5_700_000 and total["scope"] is None and total["live_calls"] == 1
    assert (session["spent_micros"], session["cap_micros"]) == (200, 1_000_000)
    assert (research["spent_micros"], research["cap_micros"]) == (200, 300_000)
    assert (other["spent_micros"], other["cap_micros"]) == (0, None)


# ---------------------------------------------------------------------------- /v1/runs details
def run_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "call_id": "T1.echo",
        "version": 1,
        "input": {"question": "hi"},
        "context": {"request_id": "req_1", "budget_scope": "test:unit", "purpose": "test"},
    }
    return {**body, **overrides}


async def test_runs_returns_the_call_result_json() -> None:
    h = make_harness()
    h.provider.queue(
        "T1.echo", ScriptedResponse(output=GOOD, usage=Usage(input_tokens=10, output_tokens=5))
    )
    async with http(app_of(h)) as client:
        resp = await client.post("/v1/runs", json=run_body(), headers=AUTH)
    data = resp.json()
    assert resp.status_code == 200
    assert data["output"] == {"answer": "hello there", "confidence": 0.5, "note": None}
    assert data["spec_id"] == "T1.echo" and data["status"] == "ok" and data["request_id"] == "req_1"


async def test_unknown_call_and_version_are_404() -> None:
    app = app_of(make_harness())
    async with http(app) as client:
        unknown = await client.post("/v1/runs", json=run_body(call_id="Z9.nope"), headers=AUTH)
        stale = await client.post("/v1/runs", json=run_body(version=99), headers=AUTH)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    assert stale.status_code == 404 and "v99" in stale.json()["error"]["message"]


async def test_version_is_optional_and_defaults_to_the_current_spec() -> None:
    h = make_harness()
    h.provider.queue("T1.echo", GOOD)
    async with http(app_of(h)) as client:
        resp = await client.post("/v1/runs", json=run_body(version=None), headers=AUTH)
    assert resp.status_code == 200


async def test_bad_bodies_are_422_envelopes_that_never_echo_the_input() -> None:
    secret = "private-" + "prompt-text-123"
    app = app_of(make_harness())
    async with http(app) as client:
        no_context = await client.post(
            "/v1/runs", json={"call_id": "T1.echo", "input": {"question": secret}}, headers=AUTH
        )
        bad_input = await client.post(
            "/v1/runs", json=run_body(input={"question": secret, "style": "huge"}), headers=AUTH
        )
    assert (
        no_context.status_code == 422 and no_context.json()["error"]["code"] == "validation_failed"
    )
    assert bad_input.status_code == 422 and bad_input.json()["error"]["code"] == "validation_failed"
    assert secret not in no_context.text and secret not in bad_input.text
    assert "style" in str(bad_input.json()["error"]["details"])


async def test_unexpected_errors_are_500_with_no_details() -> None:
    h = make_harness()
    h.provider.queue("T1.echo", RuntimeError("secret internal detail " + fake_api_key()))
    app = app_of(h)
    async with http(app, raise_app_exceptions=False) as client:
        resp = await client.post("/v1/runs", json=run_body(), headers=AUTH)
    assert resp.status_code == 500
    assert resp.json()["error"] == {
        "code": "gateway_error",
        "message": "internal gateway error",
        "details": {},
    }
    assert "secret internal detail" not in resp.text and fake_api_key() not in resp.text


# ---------------------------------------------------------------------------- error table
def test_every_error_class_has_a_stable_status_and_round_trips() -> None:
    assert set(ERROR_CLASSES) <= set(ERROR_STATUS)
    for code, cls in ERROR_CLASSES.items():
        exc = cls("boom", details={"k": 1})
        status, envelope = error_to_wire(exc)
        assert status == ERROR_STATUS[code]
        back = error_from_wire(status, envelope)
        assert type(back) is cls and back.message == "boom" and back.code == code


def test_unknown_error_codes_become_gateway_errors() -> None:
    err = error_from_wire(
        418, ErrorEnvelope(error=ErrorBody(code="teapot", message="short and stout"))
    )
    assert type(err) is GatewayError and err.details["http_status"] == 418


# ---------------------------------------------------------------------------- HttpGatewayClient transport behaviour
def mock_client(handler: httpx.MockTransport | None, **kw: object) -> HttpGatewayClient:
    return HttpGatewayClient(BASE_URL, TOKEN, transport=handler, **kw)  # type: ignore[arg-type]


async def test_client_maps_a_wrong_token_to_gateway_auth_error() -> None:
    app = app_of(make_harness())
    client = HttpGatewayClient(BASE_URL, "not-the-token", transport=httpx.ASGITransport(app=app))
    with pytest.raises(GatewayAuthError):
        await client.budget_status()
    await client.aclose()


async def test_client_handles_non_envelope_errors_and_garbage() -> None:
    def respond(status: int, body: str) -> HttpGatewayClient:
        return mock_client(httpx.MockTransport(lambda _r: httpx.Response(status, text=body)))

    with pytest.raises(GatewayError, match="HTTP 502"):
        await respond(502, "<html>bad gateway</html>").run(
            ECHO_SPEC, EchoIn(question="q"), make_ctx()
        )
    with pytest.raises(GatewayAuthError):
        await respond(401, "nope").budget_status()
    with pytest.raises(GatewayError, match="not JSON"):
        await respond(200, "not json").budget_status()
    with pytest.raises(GatewayError, match="unreadable result"):
        await respond(200, '{"call_id": 1}').run(ECHO_SPEC, EchoIn(question="q"), make_ctx())


async def test_client_maps_transport_failures() -> None:
    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def slow(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(GatewayError, match="unreachable"):
        await mock_client(httpx.MockTransport(refuse)).budget_status()
    with pytest.raises(GatewayError, match="timed out"):
        await mock_client(httpx.MockTransport(slow)).budget_status()


async def test_client_drops_the_request_when_the_caller_cancels() -> None:
    started = asyncio.Event()

    async def hang(_r: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    token = CancellationToken()
    client = mock_client(httpx.MockTransport(hang))
    task = asyncio.create_task(client.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(cancel=token)))
    await started.wait()
    token.cancel("user stopped")
    with pytest.raises(CallCancelled):
        await task


def test_default_registry_is_the_prompt_registry() -> None:
    """``create_app`` without a registry uses ``mde_prompts.registry.PromptRegistry``."""
    from mde_prompts.registry import PromptRegistry

    app = create_app(make_settings(gateway_service_token=TOKEN))
    assert isinstance(app.state.registry, PromptRegistry)
    assert app.state.gateway.provider.name == "mock"  # LLM_MODE=mock under pytest
