"""The HTTP API treats its callers as untrusted for budget purposes (review items M5, M4)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from mde_core.clock import FakeClock
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_llm_gateway.testing_specs import ECHO_SPEC, EchoIn, EchoOut, make_ctx
from mde_llm_gateway_api.app import untrusted_purpose
from mde_llm_gateway_api.testing import BASE_URL, TOKEN, Harness, build_harness

GOOD = EchoOut(answer="hello there", confidence=0.5)
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MODEL = "gpt-6-luna"


def _app(h: Harness) -> FastAPI:
    client = h.client
    assert isinstance(client, HttpGatewayClient)
    transport = client._client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return transport.app  # type: ignore[return-value]


def _run_body(purpose: str, scope: str = "test:unit") -> dict[str, object]:
    return {
        "call_id": "T1.echo",
        "version": 1,
        "input": {"question": "hi"},
        "context": {"request_id": "req_1", "budget_scope": scope, "purpose": purpose},
    }


def _fill_to_80_percent(h: Harness) -> None:
    """4.80 of 6.00 spent (the 80% tripwire: only ``must-run`` purposes may continue)."""
    guard = h.gateway.guard
    for scope, micros in (("eval:a", 1_500_000), ("eval:b", 1_500_000), ("eval:c", 1_500_000)):
        rsv = guard.reserve([scope], micros, purpose="must-run:fill", model=MODEL, mode="live")
        guard.commit_cost(rsv, micros)
    rsv = guard.reserve(["eval:d"], 300_000, purpose="must-run:fill", model=MODEL, mode="live")
    guard.commit_cost(rsv, 300_000)
    assert guard.status().tripwire == "must_run_only_80"


@pytest.mark.parametrize(
    ("purpose", "stripped"),
    [
        ("must-run:l0-smoke", True),
        ("must-run", True),
        ("MUST-RUN:x", True),
        ("  must-run:x", True),
        ("ｍｕｓｔ-run:x", True),  # fullwidth letters fold to "must-run"
        ("eval:L1", False),
        ("test", False),
        ("not-must-run", False),
    ],
)
def test_untrusted_purpose_strips_the_must_run_privilege(purpose: str, stripped: bool) -> None:
    safe = untrusted_purpose(purpose)
    assert (safe != purpose) is stripped
    assert not safe.startswith("must-run")
    if stripped:
        assert safe == f"api:{purpose}"


async def test_http_callers_cannot_use_must_run_past_the_80_percent_tripwire() -> None:
    h = build_harness("http", FakeClock())
    _fill_to_80_percent(h)
    h.provider.set_default("T1.echo", GOOD)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(h)), base_url=BASE_URL
    ) as client:
        for purpose in ("must-run:l0-smoke", "MUST-RUN:x", "eval:L1"):
            resp = await client.post("/v1/runs", json=_run_body(purpose), headers=AUTH)
            assert resp.status_code == 402, purpose
            error = resp.json()["error"]
            assert error["code"] == "budget_exceeded"
            assert error["details"]["tripwire"] == "must_run_only_80"
    assert h.provider.calls == []  # nothing was sent to the provider

    # trusted in-process code keeps the privilege on the very same gateway
    result = await h.gateway.run(ECHO_SPEC, EchoIn(question="hi"), make_ctx(purpose="must-run:x"))
    assert result.output == GOOD


async def test_http_callers_cannot_pick_an_unknown_scope_kind() -> None:
    h = build_harness("http", FakeClock())
    h.provider.set_default("T1.echo", GOOD)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(h)), base_url=BASE_URL
    ) as client:
        resp = await client.post("/v1/runs", json=_run_body("test", scope="x:1"), headers=AUTH)
    assert resp.status_code == 402
    assert resp.json()["error"]["details"]["reason"] == "unknown_scope_kind"
    assert h.provider.calls == []
