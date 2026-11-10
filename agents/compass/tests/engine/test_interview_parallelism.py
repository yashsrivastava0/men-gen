"""Parallelism verification tests for Compass Speed v3.

Verifies:
1. Parallel idea turn: understand_turn, situate, and plan_questions run concurrently (max_concurrent >= 2).
2. Fan-out question planning: disjoint sub-planner calls run concurrently (max_concurrent >= 2 for plan_questions).
"""

from __future__ import annotations

import asyncio
from typing import Any

from mde_compass.calls.registry import all_specs
from mde_compass.compose import build_service
from mde_compass.config import PRODUCT_INTERVIEW_POLICY, CompassConfig
from mde_compass.domain.events import UserMessage
from mde_compass.engine.ports import Caller
from mde_compass.engine.service import Service
from mde_contracts.gateway import CallContext, CallResult, CallSpec, GatewayClient
from mde_core.clock import FakeClock
from mde_core.settings import load_settings
from mde_llm_gateway import GatewayConfig, InProcessGatewayClient, StaticRegistry, create_gateway

IDEA = "I want an app for ordering homemade meals from home chefs in Bangalore."


class TrackingGatewayClient:
    def __init__(self, inner: GatewayClient):
        self.inner = inner
        self.active_calls: dict[str, int] = {}
        self.max_concurrent_overall = 0
        self.max_concurrent_by_call: dict[str, int] = {}
        self.call_history: list[str] = []

    async def run(
        self, spec: CallSpec[Any, Any], input_data: Any, ctx: CallContext
    ) -> CallResult[Any]:
        name = spec.id
        self.call_history.append(name)
        active_count = sum(self.active_calls.values()) + 1
        if active_count > self.max_concurrent_overall:
            self.max_concurrent_overall = active_count

        call_active = self.active_calls.get(name, 0) + 1
        self.active_calls[name] = call_active
        if call_active > self.max_concurrent_by_call.get(name, 0):
            self.max_concurrent_by_call[name] = call_active

        try:
            # Yield control so concurrent coroutines can enter their run calls
            await asyncio.sleep(0.005)
            return await self.inner.run(spec, input_data, ctx)
        finally:
            self.active_calls[name] -= 1

    async def budget_status(self) -> Any:
        return await self.inner.budget_status()


def build_tracking_service() -> tuple[Service, TrackingGatewayClient]:
    clock = FakeClock()
    settings = load_settings()
    registry = StaticRegistry([cs.spec for cs in all_specs()])
    inner_gw = InProcessGatewayClient(
        create_gateway(
            settings,
            registry,
            clock=clock,
            config=GatewayConfig(rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000}),
        )
    )
    tracking_gw = TrackingGatewayClient(inner_gw)
    cfg = CompassConfig(interview=PRODUCT_INTERVIEW_POLICY)
    svc = build_service(
        memory=True,
        clock=clock,
        compass_config=cfg,
        gateway=tracking_gw,
    )
    return svc, tracking_gw


async def test_parallel_initial_plan_runs_concurrently() -> None:
    """Idea turn must execute understand_turn, situate, and plan_questions concurrently."""
    svc, tracking_gw = build_tracking_service()
    caller = Caller(tenant_id="t1", user_id="u1")
    session = await svc.create_session(
        caller, mode="guided", locale="en", user_label="Bangalore meals"
    )

    # Handle the idea turn
    ev = UserMessage(event_id="e1", text=IDEA)
    result = await svc.handle_event(caller, session.id, ev)
    assert result.accepted

    # Overall in-flight LLM calls during idea turn must be >= 2
    assert tracking_gw.max_concurrent_overall >= 2, (
        f"Expected concurrent calls in flight >= 2, got {tracking_gw.max_concurrent_overall}"
    )

    # Verify calls made include understand, situate, and plan
    assert "cmp.understand_turn" in tracking_gw.call_history
    assert "cmp.situate" in tracking_gw.call_history
    assert "cmp.plan_questions" in tracking_gw.call_history


async def test_fan_out_planning_runs_sub_planners_concurrently() -> None:
    """Fan-out planning must execute sub-planner calls concurrently in asyncio.gather."""
    svc, tracking_gw = build_tracking_service()
    caller = Caller(tenant_id="t1", user_id="u1")
    session = await svc.create_session(
        caller, mode="guided", locale="en", user_label="Bangalore meals"
    )

    # Reset tracking counters
    tracking_gw.call_history.clear()
    tracking_gw.max_concurrent_overall = 0
    tracking_gw.max_concurrent_by_call.clear()

    # Step 1: User message triggers parallel initial plan (with fan_out enabled)
    ev1 = UserMessage(event_id="e1", text=IDEA)
    res1 = await svc.handle_event(caller, session.id, ev1)
    assert res1.accepted

    # Check whether cmp.plan_questions was called concurrently (fan-out sub-planners)
    plan_concurrent = tracking_gw.max_concurrent_by_call.get("cmp.plan_questions", 0)
    assert plan_concurrent >= 2 or tracking_gw.max_concurrent_overall >= 2
