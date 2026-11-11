"""Build a :class:`RunContext` from ports and settings (one place, used by the service, the CLI and tests)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

from mde_core.cancel import CancellationToken
from mde_core.clock import Clock
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Place, Plan
from mde_research_agent.engine.budget import FailureLedger, RunBudget
from mde_research_agent.engine.context import RunContext
from mde_research_agent.events.writer import EventStore, EventWriter
from mde_research_agent.ids import Ids
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort


def new_context(
    *,
    inp: ResearchInput,
    catalog: Catalog,
    search: SearchPort,
    fetch: FetchPort,
    gateway: object,
    events: EventStore,
    clock: Clock,
    ids: Ids,
    as_of: date,
    run_id: str | None = None,
    run_cap_micros: int | None = None,
    period_scope: str | None = None,
    language: str = "en",
    checker_vendor: str = "none",
    sleep: Callable[[float], Awaitable[None]] | None = None,
    ceilings_override: dict[str, dict[str, int]] | None = None,
) -> RunContext:
    rid = run_id or ids.new("rrn")
    klass = inp.load.load_class if inp.load else "standard"
    cs = catalog.ceilings.for_class(klass)
    soft, hard = cs.soft, cs.hard
    if ceilings_override:
        soft = soft.model_copy(update=ceilings_override.get("soft", {}))
        hard = hard.model_copy(update=ceilings_override.get("hard", {}))
    run_cancel = CancellationToken()
    runner = CallRunner(
        gateway,  # type: ignore[arg-type]
        run_id=rid,
        clock=clock,
        ids=ids,
        cancel=run_cancel,
        extra_scopes=(period_scope,) if period_scope else (),
    )
    holder: dict[str, RunContext] = {}
    writer = EventWriter(
        rid,
        events,
        catalog,
        clock=clock,
        ids=ids,
        counters=lambda: holder["ctx"].counters(),
        language=language,
    )
    ctx = RunContext(
        run_id=rid,
        input=inp,
        catalog=catalog,
        plan=Plan(targets=(), place=Place(), category_phrase=""),
        search=search,
        fetch=fetch,
        runner=runner,
        events=writer,
        budget=RunBudget(soft, hard, clock, run_cap_micros=run_cap_micros),
        failures=FailureLedger(),
        clock=clock,
        ids=ids,
        as_of=as_of,
        run_cancel=run_cancel,
        lanes_search=soft.lanes_search,
        lanes_extract=soft.lanes_extract,
        checker_vendor=checker_vendor,
        **({"sleep": sleep} if sleep else {}),  # type: ignore[arg-type]
    )
    holder["ctx"] = ctx
    return ctx
