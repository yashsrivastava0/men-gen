"""Test harness (importable: D-017): the same gateway behind the in-process client and behind the
HTTP API, used by the contract suite and the API tests. Not used by production code."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from mde_contracts.gateway import GatewayClient, LlmCallRecord
from mde_core.clock import FakeClock
from mde_llm_gateway.client import InProcessGatewayClient
from mde_llm_gateway.gateway import Gateway, GatewayConfig
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_llm_gateway.ledger import MemoryLedger
from mde_llm_gateway.providers import ScriptedProvider
from mde_llm_gateway.testing_specs import (
    build_test_gateway,
    make_settings,
    make_test_registry,
    sneaky_untrusted_tools_spec,
)
from mde_llm_gateway_api.app import create_app

TOKEN = "gw-service-token-for-tests"  # noqa: S105 - a fake, test-only value
BASE_URL = "http://gateway.test"


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[LlmCallRecord] = []

    async def record(self, record: LlmCallRecord) -> None:
        self.records.append(record)


@dataclass
class Harness:
    kind: str
    client: GatewayClient
    provider: ScriptedProvider
    gateway: Gateway
    ledger: MemoryLedger
    sink: RecordingSink
    clock: FakeClock
    closers: list[Callable[[], object]] = field(default_factory=lambda: [])


HarnessFactory = Callable[..., Harness]


def build_harness(
    kind: str,
    clock: FakeClock,
    *,
    cap_usd: str = "6.00",
    config: GatewayConfig | None = None,
) -> Harness:
    """``kind`` is ``"inprocess"`` or ``"http"`` (the real FastAPI app over ASGI, no sockets)."""
    provider = ScriptedProvider(mode="live", billable=True, clock=clock)
    ledger = MemoryLedger(clock=clock)
    sink = RecordingSink()
    registry = make_test_registry(sneaky_untrusted_tools_spec())
    settings = make_settings(gateway_service_token=TOKEN)
    gateway = build_test_gateway(
        provider,
        clock=clock,
        ledger=ledger,
        cap_usd=cap_usd,
        settings=settings,
        registry=registry,
        sink=sink,
        config=config,
    )
    if kind == "inprocess":
        client: GatewayClient = InProcessGatewayClient(gateway)
    else:
        app = create_app(settings, registry, gateway=gateway)
        client = HttpGatewayClient(BASE_URL, TOKEN, transport=httpx.ASGITransport(app=app))
    return Harness(kind, client, provider, gateway, ledger, sink, clock)
