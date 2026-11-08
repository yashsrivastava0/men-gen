"""Test helpers for the research engine: two ``GatewayClient`` fakes and request builders.

``DirectMockGateway`` runs each spec's ``mock_handler`` (``"pkg.module:function"``) directly, the
way the gateway's mock provider does, without importing the gateway. ``ScriptedGateway`` returns
queued outputs or raises queued errors per call id, counts calls and records every ``CallContext``
so tests can assert scopes, purposes and cancellation. Neither touches the network.
"""

from __future__ import annotations

import inspect
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, cast

from pydantic import BaseModel

from mde_contracts.brief import Brief, BriefFeature, BriefRow
from mde_contracts.calls import C6Output, C7Output, C8Output, S1Input
from mde_contracts.engines import ResearchRequest, RunContext
from mde_contracts.gateway import (
    BudgetStatus,
    CallContext,
    CallResult,
    CallSpec,
    Citation,
    FreeTextResult,
    GatewayClient,
    Usage,
)
from mde_contracts.policy import Limits, Policy, ResearchPolicy, WebPolicy
from mde_contracts.slots import SlotData, SlotRecord
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_research import DefaultResearchEngine, ResearchConfig

ModelOrText = BaseModel | FreeTextResult


@dataclass(frozen=True)
class RecordedCall:
    call_id: str
    input: BaseModel
    ctx: CallContext


def resolve_handler(path: str) -> Callable[[BaseModel], ModelOrText]:
    module_name, _, attr = path.partition(":")
    return cast("Callable[[BaseModel], ModelOrText]", getattr(import_module(module_name), attr))


def make_result(
    spec: CallSpec[Any, Any],
    produced: ModelOrText,
    *,
    cost_micros: int = 0,
    mode: str = "mock",
) -> CallResult[Any]:
    usage = Usage(cost_micros=cost_micros)
    if isinstance(produced, FreeTextResult):
        return CallResult(
            call_id="call_TEST",
            spec_id=spec.id,
            spec_version=spec.version,
            model="test-model",
            mode=cast("Any", mode),
            text=produced.text,
            citations=list(produced.citations),
            search_calls=produced.search_calls,
            usage=usage,
        )
    return CallResult(
        call_id="call_TEST",
        spec_id=spec.id,
        spec_version=spec.version,
        model="test-model",
        mode=cast("Any", mode),
        output=produced,
        usage=usage,
    )


def make_status(
    *,
    by_scope: Mapping[str, int] | None = None,
    spent: int = 0,
    reserved: int = 0,
    cap: int = 6_000_000,
    tripwire: str = "ok",
) -> BudgetStatus:
    return BudgetStatus(
        cap_micros=cap,
        effective_cap_micros=int(cap * 0.95),
        spent_micros=spent,
        reserved_micros=reserved,
        by_scope=dict(by_scope or {}),
        tripwire=cast("Any", tripwire),
    )


class DirectMockGateway:
    """Runs the spec's mock handler. ``cost_micros`` prices calls by spec id (default 0)."""

    def __init__(
        self,
        *,
        cost_micros: Mapping[str, int] | None = None,
        status: BudgetStatus | None = None,
        mode: str = "mock",
        check_cancel: bool = True,
        fail_budget_status: bool = False,
    ) -> None:
        self.cost_micros = dict(cost_micros or {})
        self.status = status or make_status()
        self.mode = mode
        self.check_cancel = check_cancel
        self.fail_budget_status = fail_budget_status
        self.calls: list[RecordedCall] = []

    @property
    def counts(self) -> Counter[str]:
        return Counter(c.call_id for c in self.calls)

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        if self.check_cancel:
            ctx.cancel.check()
        self.calls.append(RecordedCall(spec.id, input, ctx))
        produced = resolve_handler(spec.mock_handler)(input)
        if inspect.isawaitable(produced):
            produced = await produced
        if spec.output_model is not None:
            produced = spec.output_model.model_validate(cast("BaseModel", produced).model_dump())
        return make_result(
            spec, produced, cost_micros=self.cost_micros.get(spec.id, 0), mode=self.mode
        )

    async def budget_status(self) -> BudgetStatus:
        if self.fail_budget_status:
            raise RuntimeError("budget status unavailable")
        return self.status


@dataclass
class ScriptedGateway:
    """Queued outputs or errors per call id; unscripted calls fall through to ``fallback``.

    ``script("C7.extract", out1, ValueError("boom"), out2)`` returns ``out1`` for the first C7
    call, raises for the second, returns ``out2`` for the third. An exception *class* or instance
    is raised; anything else is the call's output. With no ``fallback`` an unscripted call fails
    the test. ``on_call`` runs before every call (for example to cancel a token).
    """

    fallback: DirectMockGateway | None = None
    status: BudgetStatus = field(default_factory=make_status)
    on_call: Callable[[RecordedCall], None] | None = None
    cost_micros: dict[str, int] = field(default_factory=dict)
    mode: str = "mock"
    check_cancel: bool = True
    fail_budget_status: bool = False
    calls: list[RecordedCall] = field(default_factory=lambda: [])
    _queues: dict[str, deque[object]] = field(default_factory=lambda: defaultdict(deque))

    def script(self, call_id: str, *items: object) -> ScriptedGateway:
        self._queues[call_id].extend(items)
        return self

    @property
    def counts(self) -> Counter[str]:
        return Counter(c.call_id for c in self.calls)

    def contexts(self, call_id: str) -> list[CallContext]:
        return [c.ctx for c in self.calls if c.call_id == call_id]

    def inputs(self, call_id: str) -> list[BaseModel]:
        return [c.input for c in self.calls if c.call_id == call_id]

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        if self.check_cancel:
            ctx.cancel.check()
        recorded = RecordedCall(spec.id, input, ctx)
        self.calls.append(recorded)
        if self.on_call is not None:
            self.on_call(recorded)
        queue = self._queues[spec.id]
        if queue:
            item = queue.popleft()
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, type) and issubclass(item, BaseException):
                raise item()
            if callable(item):
                item = cast("Callable[[BaseModel], object]", item)(input)
            return make_result(
                spec,
                cast("ModelOrText", item),
                cost_micros=self.cost_micros.get(spec.id, 0),
                mode=self.mode,
            )
        if self.fallback is None:
            raise AssertionError(f"unscripted call to {spec.id}")
        result = await self.fallback.run(spec, input, ctx)
        self.fallback.calls.pop()  # counted once, on this gateway
        return result

    async def budget_status(self) -> BudgetStatus:
        if self.fail_budget_status:
            raise RuntimeError("budget status unavailable")
        return self.status


# ----------------------------------------------------------------------------- requests

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


def fake_clock() -> FakeClock:
    return FakeClock(NOW)


def make_brief(
    *,
    summary: str = "An app for selling handmade goods",
    features: list[tuple[str, str, bool]] | None = None,
    version: int = 1,
) -> Brief:
    feats = features or [
        ("catalog_browse", "Browse products", True),
        ("payments", "Payments", True),
    ]
    rows = [
        BriefRow(
            key="features",
            title="Features",
            text="; ".join(label for _, label, on in feats if on),
            slot_ids=["scope.features"],
            features=[BriefFeature(id=i, label=label, on=on) for i, label, on in feats],
        )
    ]
    return Brief(version=version, app_summary=summary, rows=rows).with_hash()


def slot(
    slot_id: str, value: SlotData, *, state: str = "confirmed", source: str = "tap"
) -> SlotRecord:
    return SlotRecord.model_validate(
        {"slot_id": slot_id, "value": value, "state": state, "source": source}
    )


def make_slots(
    archetype: str = "commerce",
    *,
    features: list[str] | None = None,
    **extra: SlotData,
) -> dict[str, SlotRecord]:
    slots = {
        "idea.archetype": slot("idea.archetype", archetype),
        "scope.features": slot("scope.features", features or ["catalog_browse", "payments"]),
        "auth.method": slot("auth.method", "email_phone_code"),
    }
    for key, value in extra.items():
        slot_id = key.replace("__", ".")
        slots[slot_id] = slot(slot_id, value)
    return slots


def make_policy(
    *,
    enabled: bool = True,
    allow_company_name: bool = True,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    connectors: list[str] | None = None,
    org: str = "platform",
    research_budget_usd: str = "0.30",
    session_budget_usd: str = "0.50",
) -> Policy:
    return Policy(
        id="test-policy",
        org=org,
        research=ResearchPolicy(
            enabled=enabled,
            web=WebPolicy(
                allow_company_name_in_queries=allow_company_name,
                allowed_domains=allowed_domains or [],
                blocked_domains=blocked_domains or [],
            ),
            connectors=connectors or [],
        ),
        limits=Limits(
            research_budget_usd=research_budget_usd, session_budget_usd=session_budget_usd
        ),
    )


def make_request(
    *,
    archetype: str = "commerce",
    depth: str = "light",
    brief: Brief | None = None,
    slots: dict[str, SlotRecord] | None = None,
    policy: Policy | None = None,
    **fields: object,
) -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "session_id": "ses_TEST",
            "tenant_id": "tnt_TEST",
            "run_id": "run_TEST",
            "brief": brief or make_brief(),
            "slots": slots or make_slots(archetype),
            "policy": policy or make_policy(),
            "depth": depth,
            **fields,
        }
    )


def make_context(
    *, cancel: CancellationToken | None = None, with_run: bool = True
) -> tuple[RunContext, list[tuple[str, str, str, int, int]]]:
    """A ``RunContext`` plus the list its ``progress`` emitter appends to."""
    events: list[tuple[str, str, str, int, int]] = []

    async def progress(phase: str, step: str, label: str, done: int = 0, total: int = 0) -> None:
        events.append((phase, step, label, done, total))

    ctx = RunContext(
        session_id="ses_TEST",
        tenant_id="tnt_TEST",
        request_id="req_TEST",
        run_id="run_TEST" if with_run else None,
        cancel=cancel if cancel is not None else CancellationToken(),
        progress=progress,
    )
    return ctx, events


def s1_text(
    text: str, *urls: str, titles: list[str] | None = None, search_calls: int = 1
) -> FreeTextResult:
    """A free-text search result with citations for ``urls``."""
    cites = [
        Citation(url=u, title=(titles[i] if titles and i < len(titles) else f"Page {i + 1}"))
        for i, u in enumerate(urls)
    ]
    return FreeTextResult(text=text, citations=cites, search_calls=search_calls)


def s1_input(
    query: str = "common features of online shopping mobile apps", job: str = "R1"
) -> S1Input:
    return S1Input.model_validate({"query": query, "job": job})


# ----------------------------------------------------------------------------- engine scenarios


class ExtrasRequest(ResearchRequest):
    """A request carrying the two optional fields the research jobs R2 and R3 need (``references``
    and ``brand_url``): what the contract will look like once the requested additive change lands.
    The engine reads them through ``getattr`` so this subclass exercises the real code path."""

    references: list[str] = []
    brand_url: str | None = None


def make_extras_request(
    *,
    references: list[str] | None = None,
    brand_url: str | None = None,
    archetype: str = "commerce",
    depth: str = "light",
    brief: Brief | None = None,
    slots: dict[str, SlotRecord] | None = None,
    policy: Policy | None = None,
    **fields: object,
) -> ExtrasRequest:
    return ExtrasRequest.model_validate(
        {
            "session_id": "ses_TEST",
            "tenant_id": "tnt_TEST",
            "run_id": "run_TEST",
            "brief": brief or make_brief(),
            "slots": slots or make_slots(archetype),
            "policy": policy or make_policy(),
            "depth": depth,
            "references": references or [],
            "brand_url": brand_url,
            **fields,
        }
    )


def make_plan(jobs: dict[str, list[str]]) -> C6Output:
    """A C6 plan: ``{"R1": ["query one", "query two"], ...}``."""
    return C6Output.model_validate(
        {
            "jobs": [
                {"job": job, "rationale": f"Plan for {job}", "queries": queries}
                for job, queries in jobs.items()
            ]
        }
    )


def make_engine(gateway: GatewayClient, **config: object) -> DefaultResearchEngine:
    return DefaultResearchEngine(
        gateway,
        clock=fake_clock(),
        config=ResearchConfig(**config),  # type: ignore[arg-type]
    )


def full_slots(archetype: str = "commerce", **extra: SlotData) -> dict[str, SlotRecord]:
    """Slots that make every web job available: compliance flags and an integration feature."""
    return make_slots(
        archetype,
        features=["catalog_browse", "payments", "push_notifications"],
        compliance__flags=["financial_data", "precise_location"],
        regions=["in"],
        **extra,
    )


def ok_c7(*facts: tuple[str, str], impact: str = "low", kind: str = "feature") -> C7Output:
    """A C7 output of ``(statement, source_url)`` pairs."""
    return C7Output.model_validate(
        {
            "facts": [
                {
                    "statement": statement,
                    "source_url": url,
                    "kind": kind,
                    "impact": impact,
                    "confidence": "medium",
                }
                for statement, url in facts
            ]
        }
    )


def empty_c8() -> C8Output:
    return C8Output(suggestions=[])
