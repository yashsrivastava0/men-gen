"""Composition root: build the real ``Service`` (store, gateway, call runner, ports, budget, hand-off).

Mock mode is the default (``LLM_MODE=mock``): the real gateway pipeline runs with every call's mock handler, no
network, no key. Live mode needs the repo's two keys (``LLM_MODE=live`` and ``ALLOW_LIVE_LLM=1`` in the process
environment) plus ``OPENAI_API_KEY``; the Compass cap (``COMPASS_LIVE_BUDGET_USD``, at most 3.00, aim under 2.00) is enforced by
``CompassBudget`` before every call.
"""

from __future__ import annotations

import os
from dataclasses import replace

from mde_compass import AGENT_NAME
from mde_compass.budget import CompassBudget
from mde_compass.calls.base import CallRunner
from mde_compass.calls.registry import all_specs, build_ports
from mde_compass.catalog import load_catalog
from mde_compass.config import DEFAULT_CONFIG, CompassConfig, load_budget_policy
from mde_compass.engine.handoff import LoggingHandoff
from mde_compass.engine.service import Service
from mde_compass.ledger_spend import compass_spent_micros
from mde_contracts.gateway import GatewayClient
from mde_core.clock import Clock, SystemClock
from mde_core.settings import Settings, load_settings
from mde_llm_gateway import (
    GatewayConfig,
    InProcessGatewayClient,
    StaticRegistry,
    create_gateway,
)


def resolve_config(config: CompassConfig | None = None) -> CompassConfig:
    """The given config, or the default; ``COMPASS_INTERVIEW_ENGINE`` (``legacy`` or ``planner``) overrides which
    question engine runs, so the AI-planned interview can be tried before it becomes the default."""
    cfg = config or DEFAULT_CONFIG
    engine = os.environ.get("COMPASS_INTERVIEW_ENGINE", "").strip().lower()
    if engine in ("legacy", "planner"):
        cfg = replace(cfg, interview=replace(cfg.interview, engine=engine))  # type: ignore[arg-type]
    return cfg


def build_service(
    *,
    memory: bool = False,
    database_url: str | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    migrate: bool = True,
    compass_config: CompassConfig | None = None,
    gateway: GatewayClient | None = None,
) -> Service:
    """``memory=True`` uses the in-memory store (no database); otherwise PostgreSQL (migrated on start).

    ``gateway`` replaces the real gateway client (tests inject a scripted one with hand-written model outputs);
    ``compass_config`` replaces the default configuration (``COMPASS_INTERVIEW_ENGINE`` still overrides the engine)."""
    the_clock = clock or SystemClock()
    the_settings = settings or load_settings()
    catalog = load_catalog()
    mode = "live" if the_settings.llm_mode == "live" else "mock"
    budget = CompassBudget(load_budget_policy(), mode=mode)  # type: ignore[arg-type]
    if (
        mode == "live"
    ):  # the counter is per process; the ledger remembers what earlier batches spent
        budget.reconcile(compass_spent_micros(the_settings.llm_budget_ledger))

    if memory:
        from mde_compass.store import InMemoryStore

        store = InMemoryStore(clock=the_clock)
    else:
        from mde_compass.store import PostgresStore, create_store_engine
        from mde_compass.store import migrate as store_migrate

        url = database_url or the_settings.database_url
        if migrate:
            store_migrate.upgrade(url)
        store = PostgresStore(create_store_engine(url), clock=the_clock)

    # Offline the provider is free and instant: lift the per-route rate limits (they protect live calls only).
    config = (
        GatewayConfig(rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000})
        if mode == "mock"
        else None
    )
    gateway = gateway or InProcessGatewayClient(
        create_gateway(
            the_settings,
            StaticRegistry([cs.spec for cs in all_specs()]),
            clock=the_clock,
            config=config,
        )
    )
    service = Service(
        store=store,
        catalog=catalog,
        budget=budget,
        handoff=LoggingHandoff(),
        clock=the_clock,
        config=resolve_config(compass_config),
        mode_label=mode,
    )
    runner = CallRunner(gateway, clock=the_clock, guard=budget, sink=service.log_call)
    service.bind_ports(build_ports(runner, catalog))
    return service


__all__ = ["AGENT_NAME", "build_service"]
