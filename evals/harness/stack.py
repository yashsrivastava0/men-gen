"""The real product, wired in one process, offline and deterministic.

``build_stack()`` assembles what production assembles, with only the provider swapped:

* the REAL ``Gateway`` (budget guard, retries, repair, strict-schema validation) over an
  :class:`OverlayProvider`: scripted outputs for the call ids a test cares about, the call spec's own
  mock handler for everything else;
* the REAL Discover, Research and Spec Builder engines through ``InProcessGatewayClient``;
* the REAL ``FlowService`` over the in-memory store (or ``PostgresStore``) with a git project repo
  in a temp directory, a fake clock and sequential ids.

Nothing here reads ``.env``, sleeps on the wall clock or opens a socket (:class:`NetworkGuard`
proves the last one).
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, SecretStr

from evals.harness.shims import HashCompatBuilder, hash_shim_needed
from mde_content.bundle import load_bundle
from mde_context_api.budget import GatewayBudgetGate
from mde_contracts.common import Principal
from mde_contracts.engines import (
    BuildRequest,
    BuildResult,
    ResearchRequest,
    RunContext,
    SpecBuilderEngine,
)
from mde_contracts.gateway import CallResult, Citation, LlmCallRecord, Usage
from mde_contracts.policy import Policy
from mde_contracts.ports import Store
from mde_contracts.registry import CALL_IO
from mde_contracts.research import ResearchNote
from mde_core.cancel import CancellationToken
from mde_core.clock import Clock, FakeClock
from mde_core.errors import (
    BudgetExceeded,
    CircuitOpen,
    NotFound,
    StructuredOutputError,
)
from mde_core.ids import SequentialIds
from mde_core.money import usd_to_micros
from mde_core.settings import Settings
from mde_discovery.engine import DiscoverEngine
from mde_flow.gates import NoopReminders
from mde_flow.handoff import LocalGitProjectRepo, NoopFrontendEngine
from mde_flow.service import FlowService
from mde_flow.testing import InMemoryStore, TrackingStore
from mde_llm_gateway import (
    BudgetConfig,
    Gateway,
    GatewayConfig,
    InProcessGatewayClient,
    RetryPolicy,
    create_gateway,
)
from mde_llm_gateway.providers.base import ProviderCallError, ProviderRequest, ProviderResponse
from mde_llm_gateway.providers.mock import MockProvider
from mde_llm_gateway.providers.scripted import ScriptedProvider, ScriptedResponse
from mde_prompts.registry import PromptRegistry
from mde_research.engine import DefaultResearchEngine, ResearchReport
from mde_spec_builder.engine import SpecBuilderImpl

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
USER_1 = "user-1"

StoreFactory = Callable[[Clock, Callable[[str], str]], Store]

# --------------------------------------------------------------------------- scripted provider

ERROR_KINDS: tuple[str, ...] = (
    "timeout",
    "budget_exceeded",
    "structured_output_error",
    "circuit_open",
    "provider_500",
    "provider_400",
    "not_found",
)


class ScriptError(ValueError):
    """A scenario script is malformed (a typo must never silently fall back to the mock)."""


def _error_for(kind: str, message: str | None = None) -> Exception:
    if message is not None:
        if kind == "provider_400":
            return ProviderCallError(message, status=400, retryable=False)
        if kind == "provider_500":
            return ProviderCallError(message, status=500, retryable=True)
        raise ScriptError("a custom message is only supported for provider_400 and provider_500")
    if kind == "timeout":
        return ProviderCallError("timed out", retryable=True, maybe_billed=True)
    if kind == "budget_exceeded":
        return BudgetExceeded("scripted: the budget cap was reached")
    if kind == "structured_output_error":
        return StructuredOutputError("scripted: output invalid after repair")
    if kind == "circuit_open":
        return CircuitOpen("scripted: circuit open")
    if kind == "provider_500":
        return ProviderCallError("scripted 500", status=500, retryable=True)
    if kind == "provider_400":
        return ProviderCallError("scripted 400", status=400, retryable=False)
    if kind == "not_found":
        return NotFound("scripted: not found")
    raise ScriptError(f"unknown scripted error {kind!r}; use one of {ERROR_KINDS}")


class PassThrough:
    """A script entry meaning "this call runs the spec's mock handler" (it holds a place in the
    queue so a later entry lands on the Nth call of that id)."""


@dataclass(frozen=True)
class Optional_:
    """Wraps a script entry that need not be used (a failure scripted "for as long as it lasts")."""

    item: ScriptItem


ScriptItem = (
    BaseModel | Exception | ScriptedResponse | str | dict[str, object] | PassThrough | Optional_
)


def script_item(call_id: str, raw: Mapping[str, Any]) -> list[ScriptItem]:
    """Turn one YAML script entry into queue items.

    Forms (``times: N`` repeats the entry N times):

    * ``{error: timeout | budget_exceeded | structured_output_error | ...}`` raises that error
      (``message: "..."`` sets the text of ``provider_400`` / ``provider_500``);
    * ``optional: true`` marks the entry as one that may stay unused (the leftover check skips it);
    * ``{mock: true}`` lets that call run the mock handler (it only holds the place in the queue);
    * ``{raw: "text"}`` is malformed output (the gateway repairs once, then falls back);
    * ``{text: "...", citations: [{url, title}], search_calls: 1}`` is a web-search answer (S1);
    * any other mapping is the call's output; it is validated against the output model NOW so a
      typo in a scenario fails loudly instead of silently taking the fallback path
      (``invalid: true`` opts out, for a scenario that wants a schema-invalid answer).
    """
    body = dict(raw)
    times = int(body.pop("times", 1))
    if body.pop("optional", False):
        return [Optional_(i) for i in script_item(call_id, {**body, "times": times})]
    item: ScriptItem
    if body.get("mock"):
        return [PassThrough() for _ in range(times)]
    if "error" in body:
        kind = str(body["error"])
        message = str(body["message"]) if "message" in body else None
        _error_for(kind, message)  # validate the name now
        items: list[ScriptItem] = [_error_for(kind, message) for _ in range(times)]
        return items
    if "raw" in body:
        item = str(body["raw"])
    elif call_id not in CALL_IDS:
        raise ScriptError(f"unknown call id {call_id!r}; known: {sorted(CALL_IDS)}")
    elif CALL_IO[call_id][1] is None:  # S1: free text
        cites = [Citation(**c) for c in body.get("citations", [])]
        item = ScriptedResponse(
            output=str(body["text"]),
            citations=cites,
            search_calls=int(body.get("search_calls", 1)),
        )
    else:
        invalid = bool(body.pop("invalid", False))
        out_model = CALL_IO[call_id][1]
        assert out_model is not None  # S1 (free text) was handled above
        if not invalid:
            try:
                out_model.model_validate(body)
            except Exception as exc:  # pydantic ValidationError: show which call and fields
                raise ScriptError(f"scripted output for {call_id} is invalid: {exc}") from exc
        item = dict(body)
    return [item for _ in range(times)]


CALL_IDS: frozenset[str] = frozenset(CALL_IO)


class OverlayProvider:
    """``ScriptedProvider`` over ``MockProvider``: a call id with scripted items takes the next
    one, every other call (or an empty queue) runs the spec's mock handler."""

    name = "overlay"

    def __init__(
        self,
        script: Mapping[str, Sequence[ScriptItem]] | None = None,
        *,
        fallback: Any = None,
    ) -> None:
        """``fallback`` is the provider behind the script (the mock handlers by default; the live
        batch runner passes the live provider so a scripted web-search result can still be fed
        to live extraction calls)."""
        self._mock: Any = fallback if fallback is not None else MockProvider()
        self.mode: Any = getattr(self._mock, "mode", "mock")
        self.billable: bool = bool(getattr(self._mock, "billable", False))
        self._queues: dict[str, deque[ScriptItem]] = defaultdict(deque)
        self.calls: list[ProviderRequest] = []
        self.scripted_served: dict[str, int] = defaultdict(int)
        for call_id, items in (script or {}).items():
            self.queue(call_id, *items)

    def queue(self, call_id: str, *items: ScriptItem) -> None:
        self._queues[call_id].extend(items)

    def pending(self) -> dict[str, int]:
        """Scripted items not used yet, by call id."""
        return {
            cid: n
            for cid, q in self._queues.items()
            if (n := sum(1 for i in q if not isinstance(i, Optional_)))
        }

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        queue = self._queues.get(request.spec_id)
        if queue:
            item = queue.popleft()
            if isinstance(item, Optional_):
                item = item.item
            if isinstance(item, PassThrough):
                return await self._mock.complete(request)
            assert not isinstance(item, Optional_)
            self.scripted_served[request.spec_id] += 1
            scripted = ScriptedProvider()
            scripted.queue(request.spec_id, item)
            return await scripted.complete(request)
        return await self._mock.complete(request)

    def count(self, call_id: str) -> int:
        return sum(1 for c in self.calls if c.spec_id == call_id)


# --------------------------------------------------------------------------- call log


@dataclass
class CallLog:
    """``LlmCallSink`` that keeps every record (one per logical gateway call)."""

    records: list[LlmCallRecord] = field(default_factory=lambda: [])

    async def record(self, record: LlmCallRecord) -> None:
        self.records.append(record)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for r in self.records:
            out[r.spec_id] += 1
        return dict(out)

    def count(self, call_id: str) -> int:
        return sum(1 for r in self.records if r.spec_id == call_id)

    def failed(self) -> list[LlmCallRecord]:
        return [r for r in self.records if r.status == "failed"]

    def repaired(self) -> list[LlmCallRecord]:
        return [r for r in self.records if r.repaired]

    def total_usage(self) -> Usage:
        total = Usage()
        for r in self.records:
            total = total + r.usage
        return total

    def latency_ms(self) -> int:
        return sum(r.latency_ms for r in self.records)


class RecordingClient:
    """``GatewayClient`` that remembers every input and result (for assertions on what a call saw
    and for the sufficiency / token metrics) and delegates to the in-process client."""

    def __init__(self, inner: InProcessGatewayClient) -> None:
        self._inner = inner
        self.inputs: list[tuple[str, BaseModel]] = []
        self.results: list[CallResult[Any]] = []

    async def run(self, spec: Any, input: Any, ctx: Any) -> CallResult[Any]:
        self.inputs.append((spec.id, input))
        result = await self._inner.run(spec, input, ctx)
        self.results.append(result)
        return result

    async def budget_status(self) -> Any:
        return await self._inner.budget_status()

    def inputs_for(self, call_id: str) -> list[BaseModel]:
        return [i for c, i in self.inputs if c == call_id]


# --------------------------------------------------------------------------- policies


class PolicyBook:
    """``(policy_id) -> Policy`` over the packaged, inheritance-resolved policies."""

    def __init__(self, extra: Sequence[Policy] = ()) -> None:
        self._by_id: dict[str, Policy] = dict(load_bundle().policies)
        # the flow's default ids when a caller names none
        self._by_id.setdefault("enterprise-default", self._by_id["enterprise-it-services-firm"])
        for policy in extra:
            self._by_id[policy.id] = policy

    def add(self, policy: Policy) -> None:
        self._by_id[policy.id] = policy

    def __call__(self, policy_id: str) -> Policy:
        try:
            return self._by_id[policy_id]
        except KeyError:
            raise NotFound(f"unknown policy {policy_id!r}") from None


# --------------------------------------------------------------------------- holds


class Hold:
    """A rendezvous inside an engine: arm it, start the plan job in a task, wait until the engine is
    inside, send events while the run is genuinely in progress, then release (or cancel)."""

    def __init__(self) -> None:
        self.armed = False
        self.inside = asyncio.Event()
        self.release = asyncio.Event()

    def arm(self) -> None:
        self.armed = True
        self.inside = asyncio.Event()
        self.release = asyncio.Event()

    async def checkpoint(self, cancel: CancellationToken) -> None:
        if not self.armed:
            return
        self.inside.set()
        waiters = {
            asyncio.ensure_future(self.release.wait()),
            asyncio.ensure_future(cancel.wait()),
        }
        _, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        self.armed = False
        cancel.check()  # a Stop while held ends the run, exactly like a cooperative checkpoint


class HoldableResearch:
    """``ResearchEngine`` that can pause at its start (see :class:`Hold`)."""

    def __init__(self, inner: DefaultResearchEngine) -> None:
        self.inner = inner
        self.hold = Hold()

    async def run(self, req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        await self.hold.checkpoint(ctx.cancel)
        return await self.inner.run(req, ctx)

    async def run_with_report(self, req: ResearchRequest, ctx: RunContext) -> ResearchReport:
        """What the flow calls when it can: the note plus what research ignored (GAP-1)."""
        await self.hold.checkpoint(ctx.cancel)
        return await self.inner.run_with_report(req, ctx)


class HoldableBuilder:
    """``SpecBuilderEngine`` that can pause at its start (see :class:`Hold`)."""

    def __init__(self, inner: SpecBuilderEngine) -> None:
        self.inner = inner
        self.hold = Hold()
        self.results: list[BuildResult] = []  # every result, for the graders

    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult:
        await self.hold.checkpoint(ctx.cancel)
        result = await self.inner.build(req, ctx)
        self.results.append(result)
        return result


# --------------------------------------------------------------------------- the stack


@dataclass
class Stack:
    flow: FlowService
    store: TrackingStore
    gateway: Gateway
    client: RecordingClient
    provider: OverlayProvider
    calls: CallLog
    clock: FakeClock
    ids: Callable[[str], str]
    repo: LocalGitProjectRepo
    frontend: NoopFrontendEngine
    reminders: NoopReminders
    policies: PolicyBook
    projects_dir: Path
    discovery: DiscoverEngine
    research: DefaultResearchEngine
    builder: SpecBuilderImpl
    shim_active: bool = False  # BUG-1 hash shim in front of the builder
    research_hold: HoldableResearch | None = None
    builder_hold: HoldableBuilder | None = None

    def principal(
        self, user_id: str = USER_1, tenant_id: str = TENANT_A, roles: Sequence[str] = ()
    ) -> Principal:
        return Principal(tenant_id=tenant_id, user_id=user_id, roles=list(roles))

    def script_pending(self) -> dict[str, int]:
        return self.provider.pending()

    @property
    def build_results(self) -> list[BuildResult]:
        return self.builder_hold.results if self.builder_hold is not None else []


@dataclass(frozen=True)
class StackOptions:
    script: Mapping[str, Sequence[ScriptItem]] = field(default_factory=lambda: {})
    store_factory: StoreFactory | None = None
    extra_policies: Sequence[Policy] = ()
    #: tiny per-scope caps in USD (a "budget cap reached" scenario); None keeps the defaults
    session_cap_usd: str | None = None
    research_cap_usd: str | None = None
    global_cap_usd: str | None = None
    max_attempts: int = 4  # gateway retry attempts
    #: a FAKE key the gateway knows (so its log scrubbing and redaction are exercised); never real
    api_key: str | None = None
    #: an already built gateway (the live batch runner passes its live one; ``calls`` must then be
    #: the sink that gateway was created with). Everything else still runs on the fake clock.
    gateway: Gateway | None = None
    calls: CallLog | None = None
    #: id source (the live runner passes ``new_id``: budget scopes name sessions, so ids must be
    #: unique across runs; the default is deterministic)
    ids: Callable[[str], str] | None = None
    #: BUG-1 shim (see ``shims.py``): "auto" probes once per process, "on"/"off" force it
    hash_compat: Literal["auto", "on", "off"] = "auto"


def _budget_config(options: StackOptions) -> BudgetConfig:
    cap = Decimal(options.global_cap_usd or "6.00")
    base = BudgetConfig.load(cap_usd=cap)
    caps = dict(base.scope_caps_micros)
    if options.session_cap_usd is not None:
        caps["session"] = usd_to_micros(options.session_cap_usd)
    if options.research_cap_usd is not None:
        caps["research"] = usd_to_micros(options.research_cap_usd)
    return BudgetConfig(
        cap_micros=base.cap_micros,
        effective_fraction=base.effective_fraction,
        scope_caps_micros=caps,
        scope_overrides_micros=base.scope_overrides_micros,
        warn_fraction=base.warn_fraction,
        must_run_fraction=base.must_run_fraction,
        refuse_fraction=base.refuse_fraction,
        reservation_ttl_seconds=base.reservation_ttl_seconds,
    )


def build_stack(projects_dir: Path, options: StackOptions | None = None) -> Stack:
    """The whole product in one process (mock mode). ``projects_dir`` receives the hand-off repos."""
    opts = options or StackOptions()
    clock = FakeClock()
    ids: Callable[[str], str] = opts.ids or SequentialIds()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_mode="mock",
        openai_api_key=SecretStr(opts.api_key) if opts.api_key else None,
    )
    provider = OverlayProvider(opts.script)
    calls = opts.calls or CallLog()
    gateway = opts.gateway or create_gateway(
        settings,
        PromptRegistry(),
        provider=provider,
        sink=calls,
        clock=clock,
        config=GatewayConfig(retry=RetryPolicy(max_attempts=opts.max_attempts)),
        budget_config=_budget_config(opts),
    )
    client = RecordingClient(InProcessGatewayClient(gateway))
    factory = opts.store_factory or _in_memory
    store = TrackingStore(factory(clock, ids))
    book = PolicyBook(opts.extra_policies)
    discovery = DiscoverEngine(client, ids=ids, clock=clock)
    research = DefaultResearchEngine(client, clock=clock)
    builder = SpecBuilderImpl(client, clock=clock, new_id_fn=ids)
    repo = LocalGitProjectRepo(projects_dir)
    frontend = NoopFrontendEngine()
    reminders = NoopReminders()
    shim = opts.hash_compat == "on" or (opts.hash_compat == "auto" and hash_shim_needed())
    held_research = HoldableResearch(research)
    held_builder = HoldableBuilder(HashCompatBuilder(builder) if shim else builder)
    flow = FlowService(
        store=store,
        discovery=discovery,
        research=held_research,
        spec_builder=held_builder,
        project_repo=repo,
        frontend=frontend,
        policies=book,
        clock=clock,
        ids=ids,
        reminders=reminders,
        budget_gate=GatewayBudgetGate.for_gateway(gateway),  # the composition root's own gate
    )
    return Stack(
        flow=flow,
        store=store,
        gateway=gateway,
        client=client,
        provider=provider,
        calls=calls,
        clock=clock,
        ids=ids,
        repo=repo,
        frontend=frontend,
        reminders=reminders,
        policies=book,
        projects_dir=projects_dir,
        discovery=discovery,
        research=research,
        builder=builder,
        shim_active=shim,
        research_hold=held_research,
        builder_hold=held_builder,
    )


def _in_memory(clock: Clock, ids: Callable[[str], str]) -> Store:
    return InMemoryStore(clock=clock, ids=ids)


# --------------------------------------------------------------------------- network guard

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}  # noqa: S104 - comparison, not a bind


class NetworkBlocked(RuntimeError):
    """A test tried to reach a non-loopback host."""


@dataclass
class NetworkGuard:
    """Records (and blocks) every attempt to reach a non-loopback host. Loopback stays open: the
    asyncio loop itself uses a socket pair, and the integration tests talk to the local Postgres."""

    attempts: list[str] = field(default_factory=lambda: [])

    def _check(self, target: object) -> None:
        host = target[0] if isinstance(target, tuple) and target else target
        text = str(host)
        if text in _LOOPBACK or text.startswith("127."):
            return
        self.attempts.append(text)
        raise NetworkBlocked(f"offline test tried to reach {text!r}")


@contextmanager
def network_guard() -> Iterator[NetworkGuard]:
    """Block outbound connections for the duration (patches ``socket``; restores on exit)."""
    guard = NetworkGuard()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        guard._check(address)  # pyright: ignore[reportPrivateUsage]
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        guard._check(address)  # pyright: ignore[reportPrivateUsage]
        return real_connect_ex(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        guard._check(address)  # pyright: ignore[reportPrivateUsage]
        return real_create(address, *args, **kwargs)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        guard._check(host)  # pyright: ignore[reportPrivateUsage]
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        yield guard
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.create_connection = real_create  # type: ignore[assignment]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


def dump(model: BaseModel) -> str:
    """Compact JSON of a model (for failure transcripts)."""
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
