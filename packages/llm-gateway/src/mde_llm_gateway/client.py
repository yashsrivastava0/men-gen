"""In-process ``GatewayClient`` (brief 7.12): the library flavour of the interface.

The HTTP flavour (:class:`mde_llm_gateway.http_client.HttpGatewayClient`) implements the same
protocol, and one shared contract suite runs against both.
"""

from __future__ import annotations

from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, CallSpec, I, O
from mde_llm_gateway.gateway import Gateway


class InProcessGatewayClient:
    """Implements :class:`mde_contracts.gateway.GatewayClient` by calling a ``Gateway`` directly."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway

    async def run(self, spec: CallSpec[I, O], input: I, ctx: CallContext) -> CallResult[O]:
        return await self.gateway.run(spec, input, ctx)

    async def budget_status(self) -> BudgetStatus:
        return self.gateway.budget_status()
