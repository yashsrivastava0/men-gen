"""The composition root of the Research APP: live models and the live web only.

There is no way to select a test double from here: no in-memory store, no fake search or fetch, no mock model
mode. ``compose_app`` builds the real thing when the owner's two live switches (``LLM_MODE=live`` and
``ALLOW_LIVE_LLM=1``) are set; when anything is missing it still builds a service so the front end can explain, but
that service has no gateway and refuses every start-run action with a plain message (names of variables only,
never values). Tests and the developer scripts build their own services from ``assemble_service`` with doubles that
live in ``tests/support/``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_UP, Decimal
from pathlib import Path
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
    keys_present,
    load_research_settings,
    research_budget_config,
)
from mde_research_agent.ids import Ids, UlidIds
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort
from mde_research_agent.service import ResearchService
from mde_research_agent.store.ports import Store


def default_provider() -> str:
    """The measured default search provider (``data/providers.yaml``, written by the bake-off)."""
    import yaml

    from mde_research_agent.catalog.loader import DATA_DIR

    return str(
        (yaml.safe_load((DATA_DIR / "providers.yaml").read_text(encoding="utf-8")) or {}).get(
            "default", "tavily"
        )
    )


DEFAULT_PROVIDER = default_provider()


def live_switches(core: Settings) -> dict[str, bool]:
    """Which of the owner's switches are set (booleans only; the names are what the person must set)."""
    return {
        "LLM_MODE=live": core.llm_mode == "live",
        "ALLOW_LIVE_LLM=1": bool(core.allow_live_llm) and not core.forbid_live,
    }


def guard_cap_usd(core: Settings, total_usd: Decimal) -> Decimal:
    """The gateway cap this app needs so ``total_usd`` is the real ceiling.

    The guard's must-run tripwire sits at 80% of its cap and refuses ordinary calls above it, so a cap of
    ``total / 0.8`` (plus a 4% margin for reservations in flight) keeps the whole total usable. The repo clamp is
    8.00. The environment's ``LLM_BUDGET_USD`` is used when it is already higher.
    """
    need = (total_usd / Decimal("0.8") * Decimal("1.04")).quantize(
        Decimal("0.01"), rounding=ROUND_UP
    )
    return max(core.llm_budget_usd, need)


def build_ports(
    provider: str, settings: ResearchSettings, *, client: httpx.AsyncClient | None = None
) -> tuple[SearchPort | None, FetchPort, list[Any]]:
    """``(search, fetch, closers)`` for ``tavily``, ``serper`` or ``none`` (no key: no search, the app explains)."""
    from mde_research_agent.adapters.fetch.http import HttpFetcher

    closers: list[Any] = []
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


def build_gateway(
    core: Settings,
    cfg: ResearchSettings,
    clock: Clock,
    *,
    offline: bool = False,
    cassette_dir: Path | None = None,
) -> InProcessGatewayClient:
    """The in-process gateway with Research's own budget scope (one scope for a run, capped at the total budget)."""
    return InProcessGatewayClient(
        create_gateway(
            core,
            StaticRegistry([cs.spec for cs in all_specs()]),
            clock=clock,
            config=gateway_config(offline=offline),
            budget_config=research_budget_config(core.llm_budget_usd, cfg.research_live_budget_usd),
            **({"record_cassettes": True, "cassette_dir": cassette_dir} if cassette_dir else {}),
        )
    )


def assemble_service(
    *,
    store: Store,
    search: SearchPort | None,
    fetch: FetchPort,
    gateway: Any,
    core: Settings,
    cfg: ResearchSettings,
    mode: str,
    clock: Clock | None = None,
    ids: Ids | None = None,
    as_of: date | None = None,
    sleep: Any = None,
    closers: list[Any] | None = None,
    wrap_search: Any = None,
    wrap_fetch: Any = None,
    ledger_path: Path | str | None = None,
    live_budget_micros: int | None = None,
    cache_search: bool = True,
    **extra: Any,
) -> ResearchService:
    """Wire a service from explicit parts. The only place that decides nothing about doubles: callers pass them."""
    the_clock = clock or SystemClock()
    catalog = load_catalog()
    if wrap_search is not None and search is not None:
        search = wrap_search(search)
    if wrap_fetch is not None:
        fetch = wrap_fetch(fetch)
    if search is not None and cache_search:
        from mde_research_agent.adapters.search.caching import CachingSearch

        search = CachingSearch(search, fee_words=catalog.prevalence.fee_words)
    same = core.model_for_route("standard") == core.model_for_route("fast")
    return ResearchService(
        store=store,
        catalog=catalog,
        settings=cfg,
        gateway=gateway,
        search=search,
        fetch=fetch,
        clock=the_clock,
        ids=ids or UlidIds(),
        as_of=as_of,
        mode=mode,
        checker_vendor="same" if same else "different",
        ledger_path=ledger_path if ledger_path is not None else core.llm_budget_ledger,
        live_budget_micros=(
            live_budget_micros
            if live_budget_micros is not None
            else usd_to_micros(cfg.research_live_budget_usd)
        ),
        sleep=sleep,
        closers=closers,
        **extra,
    )


def build_live_service(
    *,
    store: Store,
    core: Settings | None = None,
    cfg: ResearchSettings | None = None,
    clock: Clock | None = None,
    ids: Ids | None = None,
    as_of: date | None = None,
    provider: str | None = None,
    client: httpx.AsyncClient | None = None,
    wrap_search: Any = None,
    wrap_fetch: Any = None,
    ledger_path: Path | str | None = None,
    live_budget_micros: int | None = None,
) -> ResearchService:
    """A service over the REAL gateway, search and fetch. Refuses (``LiveNotReady``) unless the switches are set."""
    core = core or load_settings()
    cfg = cfg or load_research_settings()
    missing = [name for name, ok in live_switches(core).items() if not ok]
    if missing:
        raise LiveNotReady(missing)
    the_clock = clock or SystemClock()
    chosen = provider or (
        cfg.research_search_provider
        if cfg.research_search_provider != "auto"
        else choose_provider(cfg, DEFAULT_PROVIDER)
    )
    search, fetch, closers = build_ports(chosen, cfg, client=client)
    return assemble_service(
        store=store,
        search=search,
        fetch=fetch,
        gateway=build_gateway(core, cfg, the_clock),
        core=core,
        cfg=cfg,
        mode="live",
        clock=the_clock,
        ids=ids,
        as_of=as_of,
        closers=closers,
        wrap_search=wrap_search,
        wrap_fetch=wrap_fetch,
        ledger_path=ledger_path,
        live_budget_micros=live_budget_micros,
    )


class LiveNotReady(RuntimeError):
    """The owner's live switches are not all set. Carries the NAMES that are missing, never values."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("not ready for live use: set " + ", ".join(missing))
        self.missing = missing


def readiness(core: Settings, cfg: ResearchSettings) -> list[str]:
    """Plain names of what must still be set before a run can start (empty means ready). Booleans, never values."""
    missing = [name for name, ok in live_switches(core).items() if not ok]
    keys = keys_present(cfg)
    if not any(keys.values()):
        missing.append("TAVILY_API_KEY or SERPER_API_KEY")
    return missing


def compose_app(
    cfg: ResearchSettings | None = None,
    core: Settings | None = None,
    *,
    clock: Clock | None = None,
) -> ResearchService:
    """The app's service: file store (or PostgreSQL), period ledger, spend record, inputs folder, live ports.

    Always returns a service. When the live switches or every search key are missing the service has no gateway
    (or no search) and ``start_run`` refuses with a plain message naming what to set. Nothing here can build a double.
    """
    from mde_research_agent.config import data_dir, inputs_dir, repo_root
    from mde_research_agent.inputs import InputsStore
    from mde_research_agent.inputs.estimates import Estimates
    from mde_research_agent.spend import BudgetControl, SpendRecord

    core = core or load_settings()
    cfg = cfg or load_research_settings()
    the_clock = clock or SystemClock()
    root = repo_root()
    data = data_dir(cfg)
    data.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()
    if cfg.research_store == "postgres":
        from mde_research_agent.store import migrate as store_migrate
        from mde_research_agent.store.postgres import PostgresStore, create_store_engine

        store_migrate.upgrade(core.database_url)
        store: Store = PostgresStore(create_store_engine(core.database_url))
        own_store = False
    else:
        from mde_research_agent.store.files import FileStore

        store = FileStore(data)
        own_store = True
    record = SpendRecord(
        data / "spend-record.json",
        ledger_for=lambda pid: BudgetControl.ledger_rel(root, pid),
        clock=the_clock,
    )
    control = BudgetControl(
        record,
        total_micros=usd_to_micros(cfg.research_live_budget_usd),
        tripwire_micros=usd_to_micros(cfg.research_tripwire_usd),
        repo_root=root,
    )
    control.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cap = guard_cap_usd(core, cfg.research_live_budget_usd)

    def make_gateway(
        ctl: BudgetControl, cassette_dir: Path | None = None
    ) -> InProcessGatewayClient:
        # Research's own ledger file for the period; the shared ledger is never touched. A run passes its folder
        # so its model calls are recorded as cassettes next to its search results and pages (always on).
        scoped = core.model_copy(
            update={"llm_budget_ledger": ctl.ledger_path, "llm_budget_usd": cap}
        )
        return build_gateway(scoped, cfg, the_clock, cassette_dir=cassette_dir)

    missing = readiness(core, cfg)
    switches_ok = not any(not ok for ok in live_switches(core).values())
    primary_pref = _saved_provider(data) or cfg.research_search_provider
    chosen = (
        primary_pref
        if primary_pref != "auto"
        else choose_provider(cfg, DEFAULT_PROVIDER)
    )
    http = httpx.AsyncClient(timeout=15.0, trust_env=False)
    search, fetch, _own = build_ports(chosen, cfg, client=http)
    closers: list[Any] = [http.aclose]
    other = "serper" if chosen == "tavily" else "tavily"
    second, _f, _c = build_ports(other, cfg, client=http)
    if search is not None and second is not None:  # both keys present: a failover and a limited second opinion
        from mde_research_agent.adapters.search.failover import FailoverSearch

        search = FailoverSearch(search, second)
    uploads = data / "uploads"
    estimates = Estimates()
    inputs = InputsStore(inputs_dir(cfg), catalog, estimates, uploads=uploads)
    return assemble_service(
        store=store,
        search=search,
        fetch=fetch,
        gateway=make_gateway(control) if switches_ok else None,
        core=core,
        cfg=cfg,
        mode="live",
        clock=the_clock,
        closers=closers,
        ledger_path=control.ledger_path,
        live_budget_micros=usd_to_micros(cfg.research_live_budget_usd),
        missing=missing,
        control=control,
        record=record,
        data_dir=data,
        inputs=inputs,
        estimates=estimates,
        max_concurrent=cfg.research_max_concurrent_runs,
        gateway_factory=make_gateway,
        stale_after_s=cfg.research_stale_after_s,
        own_store=own_store,
    )


def _saved_provider(data: Path) -> str | None:
    """The primary provider the owner chose in the app (``settings.json``), when it is a known one."""
    import json

    try:
        value = json.loads((data / "settings.json").read_text(encoding="utf-8")).get("primary_provider")
    except (OSError, ValueError):
        return None
    return value if value in ("auto", "tavily", "serper") else None
