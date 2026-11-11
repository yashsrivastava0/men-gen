"""Test helpers: a REAL in-process mock gateway for Research's specs, and an override gateway for scenarios.

``make_client`` runs the real gateway pipeline in mock mode (template rendering with untrusted data wrapped,
strict-schema conversion, budget guard on an in-memory ledger with Research's own scope kind, output validation, the
mock handler resolved from each spec). ``OverrideGateway`` wraps any gateway client and lets a scenario script the
output of chosen specs (or make them raise), so a test can hand-write what the extraction model "saw" while every
other call still runs the real mock pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, Usage
from mde_core.clock import Clock
from mde_core.settings import load_settings
from mde_llm_gateway import InProcessGatewayClient, StaticRegistry, create_gateway
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.calls.registry import all_specs
from mde_research_agent.config import gateway_config, research_budget_config


def make_client(
    specs: Sequence[ResearchCallSpec[Any, Any]] | None = None, *, clock: Clock | None = None
) -> InProcessGatewayClient:
    """A gateway client over the real pipeline in mock mode, serving these specs (default: all of them)."""
    settings = load_settings(llm_mode="mock")
    registry = StaticRegistry([cs.spec for cs in (specs if specs is not None else all_specs())])
    return InProcessGatewayClient(
        create_gateway(
            settings,
            registry,
            clock=clock,
            config=gateway_config(offline=True),
            budget_config=research_budget_config(settings.llm_budget_usd, "2.50"),
        )
    )


Handler = Callable[[Any], BaseModel | Exception]


class OverrideGateway:
    """Duck-typed ``GatewayClient``: scripted handlers for some spec ids, the inner gateway for the rest.

    A handler receives the call's INPUT model and returns the output model, or an exception to raise. Keys match
    a spec id exactly or as a prefix (``"rsch.extract"`` covers every ``rsch.extract.<job>``). ``calls`` records
    ``(spec_id, input)`` for every call, scripted or not, so tests can assert what was asked.
    """

    def __init__(
        self,
        inner: Any,
        overrides: dict[str, Handler] | None = None,
        *,
        mode: str = "mock",
        cost_micros: int = 0,
        tokens: tuple[int, int] = (10, 10),
    ) -> None:
        self.inner = inner
        self.overrides = dict(overrides or {})
        self.mode = mode
        self.cost_micros = cost_micros
        self.tokens = tokens
        self.calls: list[tuple[str, BaseModel]] = []

    def _handler(self, spec_id: str) -> Handler | None:
        if spec_id in self.overrides:
            return self.overrides[spec_id]
        for key, fn in self.overrides.items():
            if spec_id.startswith(key + "."):
                return fn
        return None

    async def run(self, spec: Any, inp: BaseModel, ctx: CallContext) -> CallResult[Any]:
        self.calls.append((spec.id, inp))
        handler = self._handler(spec.id)
        if handler is None:
            return await self.inner.run(spec, inp, ctx)
        out = handler(inp)
        if isinstance(out, Exception):
            raise out
        return CallResult(
            call_id=ctx.request_id,
            spec_id=spec.id,
            spec_version=spec.version,
            model="scripted",
            mode=self.mode,  # type: ignore[arg-type]
            output=out,
            usage=Usage(
                input_tokens=self.tokens[0],
                output_tokens=self.tokens[1],
                cost_micros=self.cost_micros,
            ),
            request_id=ctx.request_id,
        )

    async def budget_status(self) -> BudgetStatus:
        return await self.inner.budget_status()

    def count(self, spec_prefix: str) -> int:
        return sum(1 for sid, _ in self.calls if sid.startswith(spec_prefix))
