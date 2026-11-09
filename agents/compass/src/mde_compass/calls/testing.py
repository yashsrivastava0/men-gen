"""Test helpers for calls: a REAL in-process mock gateway and a scripted one for failure paths.

``make_client`` runs the real gateway pipeline in mock mode (template rendering with untrusted data wrapped,
strict-schema conversion, budget guard on an in-memory ledger, output validation, mock handler resolved from
``spec.mock_handler``), so call tests exercise what production runs, minus the provider.
``ScriptedGateway`` returns queued outputs or raises queued errors, for fallback and validator tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from mde_compass.calls.base import CompassCallSpec
from mde_contracts.gateway import CallContext, CallResult, Usage
from mde_core.clock import Clock
from mde_core.settings import load_settings
from mde_llm_gateway import (
    GatewayConfig,
    InProcessGatewayClient,
    StaticRegistry,
    create_gateway,
)


def make_client(
    specs: Sequence[CompassCallSpec[Any, Any]], *, clock: Clock | None = None
) -> InProcessGatewayClient:
    """A gateway client over the real pipeline in mock mode, serving exactly these specs."""
    settings = load_settings(llm_mode="mock")
    registry = StaticRegistry([cs.spec for cs in specs])
    config = GatewayConfig(rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000})
    return InProcessGatewayClient(create_gateway(settings, registry, clock=clock, config=config))


class ScriptExhausted(AssertionError):
    """A call asked for a scripted answer but none was queued: the test's script is wrong, not the code."""


class ScriptedGateway:
    """Duck-typed ``GatewayClient``: each ``run`` pops the next queued item (a model or an exception).

    ``ScriptedGateway(a, b)`` serves one queue in call order. ``ScriptedGateway(by_spec={"cmp.plan_questions":
    [a], "cmp.audit_questions": [b, c]})`` serves a queue per call id, so a scenario's hand-written outputs
    do not depend on the order in which different calls happen. A call with no queued answer raises
    :class:`ScriptExhausted`, and :meth:`assert_consumed` fails when an answer was never used.
    """

    def __init__(
        self,
        *script: BaseModel | Exception,
        by_spec: dict[str, list[BaseModel | Exception]] | None = None,
        cost_micros: int = 0,
    ) -> None:
        self.script: list[BaseModel | Exception] = list(script)
        self.by_spec: dict[str, list[BaseModel | Exception]] = {
            k: list(v) for k, v in (by_spec or {}).items()
        }
        self.cost_micros = cost_micros
        self.calls: list[tuple[str, BaseModel]] = []

    def assert_consumed(self) -> None:
        left = {k: len(v) for k, v in self.by_spec.items() if v}
        if self.script:
            left["(ordered script)"] = len(self.script)
        if left:
            raise ScriptExhausted(f"scripted answers never used: {left}")

    async def run(self, spec: Any, inp: BaseModel, ctx: CallContext) -> CallResult[Any]:
        self.calls.append((spec.id, inp))
        queue = self.by_spec.get(spec.id) if spec.id in self.by_spec else self.script
        if not queue:
            raise ScriptExhausted(f"no scripted answer left for {spec.id}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return CallResult(
            call_id=ctx.request_id,
            spec_id=spec.id,
            spec_version=int(spec.version),
            model="scripted",
            mode="mock",
            output=item,
            usage=Usage(cost_micros=self.cost_micros),
        )

    async def budget_status(self) -> Any:  # pragma: no cover - unused in tests
        return SimpleNamespace()
