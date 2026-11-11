"""The composition root: builds a :class:`ResearchService` from settings (store, gateway, search, fetch, budget)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from mde_core.clock import Clock, SystemClock
from mde_core.money import usd_to_micros
from mde_core.settings import Settings, load_settings
from mde_llm_gateway import InProcessGatewayClient, StaticRegistry, create_gateway
from mde_research_agent.calls.registry import all_specs
from mde_research_agent.catalog import load_catalog
from mde_research_agent.config import (
    ResearchSettings,
    choose_provider,
    gateway_config,
    load_research_settings,
    research_budget_config,
)
from mde_research_agent.fakes import FakeFetch, FakeSearch, FakeWorld
from mde_research_agent.ids import Ids, UlidIds
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import Store

DEFAULT_PROVIDER = "tavily"  # the measured default (data); the bake-off sets it


def build_ports(
    provider: str,
    settings: ResearchSettings,
    *,
    world: FakeWorld | None,
    client: httpx.AsyncClient | None,
) -> tuple[SearchPort | None, FetchPort, list[Any]]:
    """``(search, fetch, closers)`` for ``provider`` (``fake``, ``tavily``, ``serper`` or ``none`` for no search)."""
    closers: list[Any] = []
    if provider == "fake":
        w = world or FakeWorld()
        return FakeSearch(w), FakeFetch(w), closers
    from mde_research_agent.adapters.fetch.http import HttpFetcher

    http = client or httpx.AsyncClient(timeout=15.0, trust_env=False)
    if client is None:
        closers.append(http.aclose)
    fetch = HttpFetcher(client=http, user_agent=settings.research_user_agent)
    if provider == "tavily" and settings.tavily_api_key:
        from mde_research_agent.adapters.search.tavily import TavilyAdapter

        return (
            TavilyAdapter(settings.tavily_api_key.get_secret_value(), client=http),
            fetch,
            closers,
        )
    if provider == "serper" and settings.serper_api_key:
        from mde_research_agent.adapters.search.serper import SerperAdapter

        return (
            SerperAdapter(settings.serper_api_key.get_secret_value(), client=http),
            fetch,
            closers,
        )
    return None, fetch, closers


def build_service(
    *,
    memory: bool = True,
    store: Store | None = None,
    database_url: str | None = None,
    core_settings: Settings | None = None,
    settings: ResearchSettings | None = None,
    clock: Clock | None = None,
    ids: Ids | None = None,
    as_of: date | None = None,
    world: FakeWorld | None = None,
    gateway: Any = None,
    provider: str | None = None,
    sleep: Any = None,
    client: httpx.AsyncClient | None = None,
) -> ResearchService:
    """Everything a run needs. In mock LLM mode the search and fetch are offline fakes unless a provider is forced."""
    core = core_settings or load_settings()
    cfg = settings or load_research_settings()
    the_clock = clock or SystemClock()
    mode = "live" if core.llm_mode == "live" else "mock"
    catalog = load_catalog()
    if store is None:
        if memory:
            from mde_research_agent.store.memory import InMemoryStore

            store = InMemoryStore()
        else:
            from mde_research_agent.store import migrate as store_migrate
            from mde_research_agent.store.postgres import PostgresStore, create_store_engine

            url = database_url or core.database_url
            store_migrate.upgrade(url)
            store = PostgresStore(create_store_engine(url))
    chosen = provider or (
        cfg.research_search_provider
        if cfg.research_search_provider != "auto"
        else ("fake" if mode == "mock" else choose_provider(cfg, DEFAULT_PROVIDER))
    )
    search, fetch, closers = build_ports(chosen, cfg, world=world, client=client)
    if search is not None and chosen != "fake":
        from mde_research_agent.adapters.search.caching import CachingSearch

        search = CachingSearch(search, fee_words=catalog.prevalence.fee_words)
    the_gateway = gateway or InProcessGatewayClient(
        create_gateway(
            core,
            StaticRegistry([cs.spec for cs in all_specs()]),
            clock=the_clock,
            config=gateway_config(offline=mode == "mock"),
            budget_config=research_budget_config(core.llm_budget_usd, cfg.research_run_cap_usd),
        )
    )
    same = core.model_for_route("standard") == core.model_for_route("fast")
    return ResearchService(
        store=store,
        catalog=catalog,
        settings=cfg,
        gateway=the_gateway,
        search=search,
        fetch=fetch,
        clock=the_clock,
        ids=ids or UlidIds(),
        as_of=as_of,
        mode=mode,
        checker_vendor="same" if same else "different",
        ledger_path=core.llm_budget_ledger,
        live_budget_micros=usd_to_micros(cfg.research_live_budget_usd),
        run_cap_micros=usd_to_micros(cfg.research_run_cap_usd),
        sleep=sleep,
        closers=closers,
    )
