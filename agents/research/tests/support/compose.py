"""Test and developer-script composition: a service over DOUBLES (in-memory store, fake search and fetch, mock
gateway). The app's composition root (``mde_research_agent.compose``) cannot reach any of this."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from mde_core.clock import Clock, SystemClock
from mde_core.settings import Settings, load_settings
from mde_research_agent.compose import (
    DEFAULT_PROVIDER,
    assemble_service,
    build_gateway,
    build_ports,
)
from mde_research_agent.config import ResearchSettings, choose_provider, load_research_settings
from mde_research_agent.ids import Ids
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import Store
from support.fakes import FakeFetch, FakeSearch, FakeWorld
from support.memory_store import InMemoryStore


def build_service(
    *,
    store: Store | None = None,
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
    wrap_search: Any = None,
    wrap_fetch: Any = None,
    live_budget_micros: int | None = None,
) -> ResearchService:
    """Everything a run needs. In mock LLM mode the search and fetch are offline fakes unless a provider is forced."""
    core = core_settings or load_settings()
    cfg = settings or load_research_settings()
    the_clock = clock or SystemClock()
    mode = "live" if core.llm_mode == "live" else "mock"
    chosen = provider or (
        cfg.research_search_provider
        if cfg.research_search_provider != "auto"
        else ("fake" if mode == "mock" else choose_provider(cfg, DEFAULT_PROVIDER))
    )
    closers: list[Any] = []
    if chosen == "fake":
        w = world or FakeWorld()
        search: Any = FakeSearch(w)
        fetch: Any = FakeFetch(w)
    else:
        search, fetch, closers = build_ports(chosen, cfg, client=client)
    return assemble_service(
        store=store or InMemoryStore(),
        search=search,
        fetch=fetch,
        gateway=gateway or build_gateway(core, cfg, the_clock, offline=mode == "mock"),
        core=core,
        cfg=cfg,
        mode=mode,
        clock=the_clock,
        ids=ids,
        as_of=as_of,
        sleep=sleep,
        closers=closers,
        wrap_search=wrap_search,
        wrap_fetch=wrap_fetch,
        live_budget_micros=live_budget_micros,
        cache_search=chosen != "fake",
    )
