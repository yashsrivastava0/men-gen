"""Test and developer-script composition: a service over DOUBLES (in-memory store, fake search and fetch, mock
gateway). The app's composition root (``mde_research_agent.compose``) cannot reach any of this."""

from __future__ import annotations

from datetime import date
from pathlib import Path
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


def build_app_test_service(
    tmp_path: Path,
    *,
    inputs_dir: Path | None = None,
    world: FakeWorld | None = None,
    max_concurrent: int = 1,
    missing: list[str] | None = None,
    no_gateway: bool = False,
    total_usd: str = "5.00",
    tripwire_usd: str = "4.50",
    as_of: date | None = None,
    cost_micros: int = 8000,
) -> ResearchService:
    """The app's service shape (file store, period spend record, inputs folder, queue) over fake search and fetch.

    ``mode`` is ``live`` so the spend record, the period scope and the per-run cassettes are exercised; the gateway is
    the mock one (no network, no key). Never importable from the app.
    """
    from mde_core.money import usd_to_micros
    from mde_research_agent.catalog import load_catalog
    from mde_research_agent.inputs import InputsStore
    from mde_research_agent.inputs.estimates import Estimates
    from mde_research_agent.spend import BudgetControl, SpendRecord
    from mde_research_agent.store.files import FileStore

    core = load_settings(llm_mode="mock")
    cfg = ResearchSettings()
    clock = SystemClock()
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    w = world or FakeWorld(provider_content_every=3)
    w.search_cost_micros = cost_micros
    record = SpendRecord(
        data / "spend-record.json",
        ledger_for=lambda pid: BudgetControl.ledger_rel(root, pid),
        clock=clock,
    )
    control = BudgetControl(
        record,
        total_micros=usd_to_micros(total_usd),
        tripwire_micros=usd_to_micros(tripwire_usd),
        repo_root=root,
    )

    def make_gateway(_ctl: BudgetControl, cassette_dir: Path | None = None) -> Any:
        return build_gateway(core, cfg, clock, offline=True)

    estimates = Estimates()
    catalog = load_catalog()
    inputs = InputsStore(
        inputs_dir or tmp_path / "inputs", catalog, estimates, uploads=data / "uploads"
    )
    return assemble_service(
        store=FileStore(data),
        search=FakeSearch(w),
        fetch=FakeFetch(w),
        gateway=None if no_gateway else make_gateway(control),
        core=core,
        cfg=cfg,
        mode="live",
        clock=clock,
        as_of=as_of or date(2026, 11, 11),
        cache_search=False,
        missing=missing,
        control=control,
        record=record,
        data_dir=data,
        inputs=inputs,
        estimates=estimates,
        max_concurrent=max_concurrent,
        gateway_factory=make_gateway,
        own_store=True,
        ledger_path=control.ledger_path,
        live_budget_micros=usd_to_micros(total_usd),
    )
