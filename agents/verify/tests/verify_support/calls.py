"""A `ModelCall` double that runs each call through the REAL gateway pipeline in mock mode (template rendering with the
untrusted data wrapped, strict-schema conversion, the call's own mock handler, output validation), then the call's
local validators, with the deterministic fallback flagged on any failure, as Spec's runner does.

`overrides` scripts the output of chosen call ids (a handler returns the output model, or an exception to raise), so a
test can hand-write what a critic "saw" while every other call still runs the real mock pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from mde_contracts.gateway import CallContext
from mde_core.ids import new_id
from mde_core.settings import load_settings
from mde_llm_gateway import (
    BudgetConfig,
    GatewayConfig,
    InProcessGatewayClient,
    StaticRegistry,
    create_gateway,
)
from mde_spec_core.verify_port import CallOutcome, PortCallSpec
from mde_verify_agent.calls.registry import ALL_SPECS

Handler = Callable[[Any], Any]


class GatewayCall:
    def __init__(self, overrides: dict[str, Handler] | None = None, cost_micros: int = 0) -> None:
        settings = load_settings(llm_mode="mock")
        registry = StaticRegistry([s.spec for s in ALL_SPECS])
        self.client = InProcessGatewayClient(
            create_gateway(
                settings,
                registry,
                config=GatewayConfig(
                    concurrency={"fast": 8, "standard": 8, "deep": 4},
                    rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000},
                    max_untrusted_chars=120_000,
                ),
                budget_config=BudgetConfig.load(cap_usd=settings.llm_budget_usd),
            )
        )
        self.overrides = dict(overrides or {})
        self.cost_micros = cost_micros
        self.calls: list[tuple[str, tuple[str, ...], str]] = []
        self.inputs: list[Any] = []

    async def __call__(
        self, spec: PortCallSpec, inp: Any, *, node_ids: Sequence[str] = (), step: str = ""
    ) -> CallOutcome:
        self.calls.append((spec.id, tuple(node_ids), step))
        self.inputs.append(inp)
        handler = self.overrides.get(spec.id)
        try:
            if handler is not None:
                out = handler(inp)
                if isinstance(out, Exception):
                    raise out
            else:
                ctx = CallContext(
                    request_id=new_id("call"),
                    budget_scope="test:verify",
                    purpose=f"verify.{spec.id}",
                )
                out = (await self.client.run(spec.spec, inp, ctx)).output
        except Exception:  # a model-side failure: the deterministic fallback, flagged
            return CallOutcome(spec.fallback(inp), used_fallback=True, flagged=True)
        errors = [e for v in spec.validators for e in v(inp, out)]
        if errors:
            return CallOutcome(spec.fallback(inp), used_fallback=True, flagged=True)
        return CallOutcome(out, cost_micros=self.cost_micros)
