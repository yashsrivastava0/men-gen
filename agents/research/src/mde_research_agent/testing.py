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


# --- scenario harness --------------------------------------------------------------------------------------------

import json  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402

from mde_core.clock import FakeClock  # noqa: E402
from mde_research_agent.assemble.output import assemble_output  # noqa: E402
from mde_research_agent.calls.extract import ExtractIn, extract_out_model  # noqa: E402
from mde_research_agent.catalog import load_catalog  # noqa: E402
from mde_research_agent.domain.events import Event  # noqa: E402
from mde_research_agent.domain.research import ResearchOutput  # noqa: E402
from mde_research_agent.engine.context import RunContext  # noqa: E402
from mde_research_agent.engine.director import Director, DirectorResult  # noqa: E402
from mde_research_agent.engine.factory import new_context  # noqa: E402
from mde_research_agent.events.writer import MemoryEventStore  # noqa: E402
from mde_research_agent.fakes import FakeFetch, FakeSearch, FakeWorld  # noqa: E402
from mde_research_agent.ids import CounterIds  # noqa: E402
from mde_research_agent.intake import read_export  # noqa: E402
from mde_research_agent.validators import ValidationContext, Violation, run_all  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
AS_OF = date(2026, 11, 11)


def load_fixture(name: str) -> dict:
    """A fixture export by stem, from ``exports/`` or ``synthetic/``."""
    for folder in ("exports", "synthetic"):
        path = FIXTURES / folder / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(name)


@dataclass
class EngineRun:
    ctx: RunContext
    result: DirectorResult
    output: ResearchOutput
    evidence: dict
    events: list[Event]
    search: FakeSearch
    fetch: FakeFetch
    gateway: OverrideGateway
    director: Director
    violations: list[Violation] = field(default_factory=list)

    @property
    def research(self) -> dict:
        return self.output.to_json_dict()

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


async def run_engine(
    export: dict,
    *,
    world: FakeWorld | None = None,
    overrides: dict[str, Handler] | None = None,
    ceilings: dict[str, dict[str, int]] | None = None,
    clock: FakeClock | None = None,
    skip_after_searches: int | None = None,
    use_model: bool = True,
    validate: bool = True,
    mode: str = "mock",
    checker_vendor: str = "same",
    sleep=None,
    watch_interval: float = 0.01,
) -> EngineRun:
    """Plan, run, assemble and validate one run in process (no HTTP, no store) over fakes."""
    cat = load_catalog()
    inp = read_export(export, cat)
    the_clock = clock or FakeClock()
    the_world = world or FakeWorld(provider_content_every=3, clock=the_clock)
    if the_world.clock is None:
        the_world.clock = the_clock
    search, fetch = FakeSearch(the_world), FakeFetch(the_world)
    gateway = OverrideGateway(make_client(), overrides, mode=mode)

    async def no_sleep(_: float) -> None:
        return None

    store = MemoryEventStore()
    ctx = new_context(
        inp=inp,
        catalog=cat,
        search=search,
        fetch=fetch,
        gateway=gateway,
        events=store,
        clock=the_clock,
        ids=CounterIds(),
        as_of=AS_OF,
        checker_vendor=checker_vendor,
        sleep=sleep or no_sleep,
        ceilings_override=ceilings,
    )
    director = Director(ctx)
    director.watch_interval = watch_interval
    if skip_after_searches is not None:
        original = search.search

        async def counting(request):  # type: ignore[no-untyped-def]
            res = await original(request)
            if len(search.requests) >= skip_after_searches:
                director.skip()
            return res

        search.search = counting  # type: ignore[method-assign]
    result = await director.execute()
    output, evidence = await assemble_output(
        ctx, result, use_model=use_model and not ctx.run_cancel.cancelled
    )
    run = EngineRun(
        ctx, result, output, evidence, list(store.events), search, fetch, gateway, director
    )
    if validate:
        cs = cat.ceilings.for_class(inp.load.load_class if inp.load else "standard")
        run.violations = run_all(
            ValidationContext(
                output=output.to_json_dict(),
                evidence=evidence,
                inp=inp,
                catalog=cat,
                plan=ctx.plan,
                events=run.events,
                ceilings_hard=(
                    (ceilings or {}).get("hard")
                    and {**cs.hard.model_dump(), **(ceilings or {}).get("hard", {})}
                )
                or cs.hard.model_dump(),
                slice_cap_tokens=cs.soft.slice_tokens,
                research_cap_tokens=cs.hard.research_tokens,
            )
        )
    return run


# --- hand-written extraction ----------------------------------------------------------------------------------------


def atom(
    kind: str,
    quote: str,
    *,
    key: str | None = None,
    label: str | None = None,
    claim: str | None = None,
    impact: str = "normal",
    published_on: str | None = None,
    **payload: object,
) -> tuple[str, dict]:
    """One extracted item as a model would return it: common fields plus the kind's own fields (defaults filled)."""
    from mde_research_agent.calls.extract import synthesize_payload

    base = synthesize_payload(kind, quote)
    base.update(payload)
    words = quote.split()
    common = {
        "norm_key": key or " ".join(words[:3]).lower(),
        "label": label or " ".join(words[:5]),
        "claim": claim or quote,
        "quote": quote,
        "impact": impact,
        "published_on": published_on,
    }
    return kind, {**common, **base}


def extractor(build) -> Handler:  # type: ignore[no-untyped-def]
    """An extraction handler from ``build(inp: ExtractIn) -> list[(kind, atom dict)]``; only the job's kinds are kept."""

    def handle(inp: ExtractIn):  # type: ignore[no-untyped-def]
        data: dict[str, list[dict]] = {k: [] for k in inp.kinds}
        for kind, a in build(inp):
            if kind in data:
                data[kind].append(a)
        return extract_out_model(inp.job).model_validate(data)

    return handle


def rule_extractor(pattern: str = r"[^.]*\bfees?\b[^.]*\d[^.]*\.") -> Handler:
    """Extract a ``rule`` item from every sentence that matches ``pattern`` (default: a sentence with a fee and a number)."""
    import re

    rx = re.compile(pattern, re.I)

    def build(inp: ExtractIn):  # type: ignore[no-untyped-def]
        out = []
        for m in rx.finditer(inp.passages):
            sentence = " ".join(m.group(0).split())
            nums = re.findall(r"\d+(?:\.\d+)?", sentence)
            out.append(
                atom(
                    "rule",
                    sentence,
                    key="payment fee",
                    label="Payment fee",
                    impact="high",
                    statement=sentence,
                    subject="payment fee",
                    value=nums[0] if nums else None,
                    unit="percent",
                    authority=None,
                )
            )
        return out

    return extractor(build)
