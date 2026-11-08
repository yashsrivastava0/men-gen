"""The composition root: the one place that wires every engine together (BRIEF 5.2: apps compose).

``build_app_state(settings)`` assembles, from settings alone:

* the LLM gateway: ``GATEWAY_MODE=inprocess`` -> ``create_gateway(settings, PromptRegistry())``
  (mock mode needs no key and no network); ``http`` -> ``HttpGatewayClient(GATEWAY_URL, token)``,
* the three engines: ``DiscoverEngine``, ``DefaultResearchEngine`` and the Spec Builder
  (``mde_spec_builder.engine.SpecBuilderImpl``, imported lazily; a missing builder is a clear
  ``ConfigError``; ``fake_builder=True`` / ``SPEC_BUILDER=fake`` uses the scripted
  ``mde_flow.testing.FakeSpecBuilder`` instead, for dev and tests),
* ``PostgresStore`` over the pooled async engine (or any ``Store`` you pass in),
* the policy catalog, ``LocalGitProjectRepo(PROJECTS_DIR)`` and ``NoopFrontendEngine``,
* ``FlowService`` over all of them (with the budget gate, ``mde_context_api.budget``, over the
  gateway's budget status), and the optional in-process job worker,
* the ledger integrity check ``/readyz`` runs (in-process gateway only: the ledger is the
  gateway's, ``LedgerGuard`` / ``integrity_problem()`` in ``mde_llm_gateway``),
* the abuse limits (``mde_context_api.limits``: concurrent streams, session creation) and the
  policy bindings check (``TENANT_POLICIES`` / ``API_TOKENS`` entries must name real policies).

Nothing here connects to the database: the engine's pool connects on first use, so building the
state (and the OpenAPI document) works with no database running.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import cast

from sqlalchemy import text

from mde_context_api.auth import Caller
from mde_context_api.bindings import PolicyBinding, merge
from mde_context_api.budget import budget_gate_for
from mde_context_api.limits import AbuseLimits
from mde_context_api.policies import PolicyCatalog
from mde_context_api.profiles import OrgProfiles
from mde_context_api.settings import ApiSettings
from mde_context_api.worker import BackgroundWorker
from mde_contracts.engines import SpecBuilderEngine
from mde_contracts.gateway import GatewayClient
from mde_contracts.ports import FrontendEnginePort, ProjectRepoPort, Store
from mde_core.clock import Clock, SystemClock
from mde_core.errors import ConfigError
from mde_core.ids import new_id
from mde_core.logging import register_secrets
from mde_core.money import usd_to_micros
from mde_discovery.engine import DiscoverEngine
from mde_flow.handoff import LocalGitProjectRepo, NoopFrontendEngine
from mde_flow.service import FlowService
from mde_llm_gateway import InProcessGatewayClient, create_gateway
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_persistence.db import create_engine
from mde_persistence.llm_calls import PgLlmCallSink
from mde_persistence.store import PostgresStore
from mde_prompts.registry import PromptRegistry
from mde_research.engine import DefaultResearchEngine

Closer = Callable[[], Awaitable[None]]
PING_TIMEOUT_SECONDS = 3.0

SPEC_BUILDER_MISSING = (
    "The Spec Builder (mde_spec_builder.engine.SpecBuilderImpl) is not available in this build. "
    "Start with --fake-builder (or SPEC_BUILDER=fake) to use the scripted builder for development."
)


@dataclass
class AppState:
    """Everything a request needs, built once per process."""

    settings: ApiSettings
    store: Store
    flow: FlowService
    gateway: GatewayClient
    policies: PolicyCatalog
    org_profiles: OrgProfiles
    ping: Callable[[], Awaitable[None]]  # raises when the database is unreachable
    builder_kind: str
    #: the budget ledger's integrity check: the problem text, or None when it is sound. None here
    #: means the ledger is not ours to check (an HTTP gateway keeps its own).
    ledger_problem: Callable[[], str | None] | None = None
    worker: BackgroundWorker | None = None
    clock: Clock = field(default_factory=SystemClock)
    #: concurrent-stream and session-creation limits (built from the settings and the clock)
    limits: AbuseLimits = field(init=False)
    _closers: list[Closer] = field(default_factory=list[Closer])

    def __post_init__(self) -> None:
        self.limits = AbuseLimits(self.settings, self.clock)

    def binding_for(self, caller: Caller) -> PolicyBinding | None:
        """What this caller may start: the token's binding and the tenant's, both must hold."""
        return merge(caller.policy, self.settings.tenant_policies.get(caller.principal.tenant_id))

    def start_worker(self) -> BackgroundWorker:
        """Start the in-process job worker (idempotent)."""
        if self.worker is None:
            self.worker = BackgroundWorker(self.store, self.flow, self.settings)
        self.worker.start()
        return self.worker

    async def stop_worker(self) -> None:
        if self.worker is not None:
            await self.worker.stop()

    async def aclose(self) -> None:
        """Stop the worker, then release the pool and clients (in reverse order of creation)."""
        await self.stop_worker()
        for closer in reversed(self._closers):
            await closer()
        self._closers.clear()


def build_spec_builder(
    settings: ApiSettings,
    gateway: GatewayClient,
    *,
    fake: bool,
    clock: Clock,
    ids: Callable[[str], str],
) -> tuple[SpecBuilderEngine, str]:
    """The Spec Builder engine and its kind (``real`` or ``fake``)."""
    if fake or settings.spec_builder == "fake":
        from mde_flow.testing import FakeSpecBuilder

        return FakeSpecBuilder(ids=ids), "fake"
    try:
        module = import_module("mde_spec_builder.engine")
        impl = module.SpecBuilderImpl
    except (ImportError, AttributeError) as exc:
        raise ConfigError(SPEC_BUILDER_MISSING) from exc
    return cast("SpecBuilderEngine", impl(gateway, clock=clock)), "real"


def build_gateway(
    settings: ApiSettings, store: PostgresStore | None, clock: Clock, closers: list[Closer]
) -> GatewayClient:
    if settings.gateway_mode == "http":
        token = settings.gateway_service_token
        if not settings.gateway_url or token is None:
            raise ConfigError("GATEWAY_MODE=http needs GATEWAY_URL and GATEWAY_SERVICE_TOKEN")
        client = HttpGatewayClient(settings.gateway_url, token.get_secret_value())
        closers.append(client.aclose)
        return client
    sink = PgLlmCallSink(store.engine, clock=clock) if store is not None else None
    gateway = create_gateway(settings, PromptRegistry(), sink=sink, clock=clock)
    return InProcessGatewayClient(gateway)


async def _no_ping() -> None:
    return None


def ledger_check(client: GatewayClient) -> Callable[[], str | None] | None:
    """``integrity_problem()`` of the in-process gateway's ledger (a deleted or truncated ledger
    would silently reset the USD 6.00 budget). A ledger that cannot be tampered with (memory)
    always answers "sound". Another client has no ledger of ours: no check."""
    if not isinstance(client, InProcessGatewayClient):
        return None
    check = getattr(client.gateway.guard.ledger, "integrity_problem", None)
    if callable(check):
        return cast("Callable[[], str | None]", check)
    return lambda: None


def _pg_ping(store: PostgresStore) -> Callable[[], Awaitable[None]]:
    async def ping() -> None:
        async def probe() -> None:
            async with store.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(probe(), timeout=PING_TIMEOUT_SECONDS)

    return ping


def _check_bindings(settings: ApiSettings, policies: PolicyCatalog) -> None:
    """Every policy a ``TENANT_POLICIES`` or ``API_TOKENS`` entry names must exist (a deployment
    error is found at start-up, not on someone's first request)."""
    for tenant, binding in settings.tenant_policies.items():
        policies.validate_binding(binding, f"TENANT_POLICIES[{tenant}]")
    table = settings.token_table
    for number, (_, entry) in enumerate(table.entries if table else (), start=1):
        own = entry.binding()
        if own is not None:
            policies.validate_binding(own, f"API_TOKENS entry {number}")


async def build_app_state(
    settings: ApiSettings,
    *,
    fake_builder: bool = False,
    store: Store | None = None,
    gateway: GatewayClient | None = None,
    project_repo: ProjectRepoPort | None = None,
    frontend: FrontendEnginePort | None = None,
    clock: Clock | None = None,
    ids: Callable[[str], str] | None = None,
) -> AppState:
    """Wire the whole service from ``settings``.

    The keyword arguments replace one collaborator (tests pass an in-memory store, a scripted
    gateway, a deterministic clock and ids). The job worker is not started here: the application's
    lifespan starts it (``WORKER_ENABLED``), and ``AppState.start_worker()`` does it by hand.
    """
    register_secrets(settings.secret_values())  # never let a key reach a log line
    the_clock: Clock = clock or SystemClock()
    the_ids = ids or new_id
    closers: list[Closer] = []
    try:
        pg_store: PostgresStore | None = None
        if store is None:
            engine = create_engine(settings)
            closers.append(engine.dispose)
            pg_store = PostgresStore(engine, clock=the_clock)
            store = pg_store
        elif isinstance(store, PostgresStore):
            pg_store = store

        the_gateway = gateway or build_gateway(settings, pg_store, the_clock, closers)
        builder, builder_kind = build_spec_builder(
            settings, the_gateway, fake=fake_builder, clock=the_clock, ids=the_ids
        )
        policies = PolicyCatalog(
            guided_default=settings.default_guided_policy,
            enterprise_default=settings.default_enterprise_policy,
        )
        _check_bindings(settings, policies)  # a binding to an unknown policy stops the start-up
        flow = FlowService(
            store=store,
            discovery=DiscoverEngine(the_gateway, ids=the_ids, clock=the_clock),
            research=DefaultResearchEngine(the_gateway, clock=the_clock),
            spec_builder=builder,
            project_repo=project_repo or LocalGitProjectRepo(settings.projects_dir),
            frontend=frontend or NoopFrontendEngine(),
            policies=policies,
            clock=the_clock,
            ids=the_ids,
            budget_gate=budget_gate_for(
                the_gateway,
                plan_reserve_micros=usd_to_micros(settings.budget_plan_reserve_usd),
            ),
        )
    except BaseException:
        for closer in reversed(closers):  # nothing was handed out yet: release what was opened
            await closer()
        raise
    state = AppState(
        settings=settings,
        store=store,
        flow=flow,
        gateway=the_gateway,
        policies=policies,
        org_profiles=OrgProfiles(store),
        ping=_pg_ping(pg_store) if pg_store is not None else _no_ping,
        builder_kind=builder_kind,
        ledger_problem=ledger_check(the_gateway),
        clock=the_clock,
        _closers=closers,
    )
    return state
