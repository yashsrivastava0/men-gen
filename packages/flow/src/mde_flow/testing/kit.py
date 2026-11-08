"""``make_flow``: a fully wired ``FlowService`` over the in-memory store and scripted engines.

``make_flow_kit`` returns the service together with every collaborator (so a test can script the
engines, read the store, look at what the project repo and the frontend were told). ``Driver``
plays one user's conversation against a session with a few readable methods.

The store is pluggable: by default an ``InMemoryStore``; pass ``store_factory`` (or install one with
``use_store_factory``) to run the same scenario over ``PostgresStore``. ``kit.peek`` reads what the
flow stored through the ordinary repositories, so assertions work on either.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

from mde_contracts.common import GateKind, Mode, Principal
from mde_contracts.events import (
    GateDecision,
    GateEvent,
    InboundEvent,
    OptionSelected,
    OutboundEvent,
    UiAction,
    UiActionName,
    UserMessage,
)
from mde_contracts.policy import Policy
from mde_contracts.ports import ProjectRepoPort, Store
from mde_core.clock import Clock, FakeClock
from mde_core.ids import SequentialIds
from mde_flow.gates import NoopReminders
from mde_flow.handoff import NoopFrontendEngine
from mde_flow.models import SessionSnapshot
from mde_flow.service import FlowService
from mde_flow.testing.builders import PolicyBook, enterprise_policy, guided_policy
from mde_flow.testing.fakes import (
    FakeBudgetGate,
    FakeDiscoveryEngine,
    FakeProjectRepo,
    FakeResearchEngine,
    FakeSpecBuilder,
)
from mde_flow.testing.peek import Peek, TrackingStore
from mde_flow.testing.store import InMemoryStore

E = TypeVar("E", bound=OutboundEvent)

StoreFactory = Callable[[Clock, Callable[[str], str]], Store]
"""Builds the kit's store from its clock and id source (an ``InMemoryStore`` by default)."""

_default_store_factory: StoreFactory | None = None


def in_memory_store(clock: Clock, ids: Callable[[str], str]) -> Store:
    return InMemoryStore(clock=clock, ids=ids)


@contextmanager
def use_store_factory(factory: StoreFactory) -> Generator[None]:
    """Make every ``make_flow_kit()`` in the block build its store with ``factory`` (tests run
    the same scenarios over the in-memory fake and over ``PostgresStore`` this way)."""
    global _default_store_factory
    previous, _default_store_factory = _default_store_factory, factory
    try:
        yield
    finally:
        _default_store_factory = previous


@dataclass
class FlowKit:
    service: FlowService
    store: TrackingStore
    discovery: FakeDiscoveryEngine
    research: FakeResearchEngine
    builder: FakeSpecBuilder
    budget: FakeBudgetGate
    repo: ProjectRepoPort
    frontend: NoopFrontendEngine
    reminders: NoopReminders
    clock: FakeClock
    ids: SequentialIds
    policies: PolicyBook

    @property
    def peek(self) -> Peek:
        return Peek(self.store)

    @property
    def memory(self) -> InMemoryStore:
        """The underlying ``InMemoryStore`` (its synchronous test views); in-memory kits only."""
        inner = self.store.inner
        assert isinstance(inner, InMemoryStore), "this kit runs over another store"
        return inner

    @property
    def fake_repo(self) -> FakeProjectRepo:
        assert isinstance(self.repo, FakeProjectRepo)
        return self.repo

    async def driver(
        self,
        *,
        mode: Mode = "guided",
        policy_id: str | None = None,
        principal: Principal | None = None,
    ) -> Driver:
        return await Driver.start(self, mode=mode, policy_id=policy_id, principal=principal)


def make_flow_kit(
    *,
    policies: Sequence[Policy] = (),
    project_repo: ProjectRepoPort | None = None,
    ask_slot: str | None = None,
    store_factory: StoreFactory | None = None,
    budget_gate: FakeBudgetGate | None = None,
) -> FlowKit:
    clock = FakeClock()
    ids = SequentialIds()
    factory = store_factory or _default_store_factory or in_memory_store
    store = TrackingStore(factory(clock, ids))
    book = PolicyBook(
        guided_policy(),
        enterprise_policy(policy_id="enterprise-default"),
        *policies,
    )
    discovery = FakeDiscoveryEngine(ids=ids)
    research = FakeResearchEngine()
    builder = FakeSpecBuilder(ids=ids, ask_slot=ask_slot)
    repo: ProjectRepoPort = project_repo or FakeProjectRepo()
    frontend = NoopFrontendEngine()
    reminders = NoopReminders()
    budget = budget_gate or FakeBudgetGate()
    service = FlowService(
        store=store,
        discovery=discovery,
        research=research,
        spec_builder=builder,
        project_repo=repo,
        frontend=frontend,
        policies=book,
        clock=clock,
        ids=ids,
        reminders=reminders,
        budget_gate=budget,
    )
    return FlowKit(
        service,
        store,
        discovery,
        research,
        builder,
        budget,
        repo,
        frontend,
        reminders,
        clock,
        ids,
        book,
    )


def make_flow(
    *,
    policies: Sequence[Policy] = (),
    project_repo: ProjectRepoPort | None = None,
    ask_slot: str | None = None,
) -> FlowService:
    """A wired ``FlowService`` (guided-default and enterprise-default policies available)."""
    return make_flow_kit(policies=policies, project_repo=project_repo, ask_slot=ask_slot).service


def principal(
    user_id: str = "user-1", tenant_id: str = "tenant-a", roles: Sequence[str] = ()
) -> Principal:
    return Principal(tenant_id=tenant_id, user_id=user_id, roles=list(roles))


class Driver:
    """One user's conversation against one session."""

    def __init__(self, kit: FlowKit, who: Principal, session_id: str) -> None:
        self.kit = kit
        self.who = who
        self.session_id = session_id
        self.events: list[OutboundEvent] = []  # everything received, in order

    @classmethod
    async def start(
        cls,
        kit: FlowKit,
        *,
        mode: Mode = "guided",
        policy_id: str | None = None,
        principal: Principal | None = None,
    ) -> Driver:
        who = principal or Principal(tenant_id="tenant-a", user_id="user-1", roles=[])
        rec, events = await kit.service.create_session(who, mode, policy_id)
        d = cls(kit, who, rec.id)
        d.events.extend(events)
        return d

    # -- sending ---------------------------------------------------------------------------
    def eid(self) -> str:
        return self.kit.ids("evt")

    async def send(
        self, event: InboundEvent, *, who: Principal | None = None
    ) -> list[OutboundEvent]:
        out = await self.kit.service.handle_event(who or self.who, self.session_id, event)
        self.events.extend(out)
        return out

    async def say(self, text: str) -> list[OutboundEvent]:
        return await self.send(UserMessage(event_id=self.eid(), text=text))

    async def ui(
        self,
        action: UiActionName,
        target: str | None = None,
        value: bool | None = None,
        *,
        who: Principal | None = None,
    ) -> list[OutboundEvent]:
        return await self.send(
            UiAction(event_id=self.eid(), action=action, target=target, value=value), who=who
        )

    async def tap(self, option_id: str = "A", *, you_decide: bool = False) -> list[OutboundEvent]:
        snap = await self.snapshot()
        assert snap.pending_card is not None, "no pending card to tap"
        return await self.send(
            OptionSelected(
                event_id=self.eid(),
                card_instance_id=snap.pending_card.instance_id,
                option_ids=[] if you_decide else [option_id],
                you_decide=you_decide,
            )
        )

    async def decide(
        self,
        gate: GateKind,
        decision: str = "approve",
        *,
        version: int | None = None,
        hash: str | None = None,
        comment: str | None = None,
        who: Principal | None = None,
        role: str | None = None,
        event_id: str | None = None,
    ) -> list[OutboundEvent]:
        shown = self.last_gate(gate)
        assert shown is not None or (version is not None and hash is not None)
        event = GateDecision.model_validate(
            {
                "event_id": event_id or self.eid(),
                "gate": gate,
                "decision": decision,
                "version": version if version is not None else shown.version,  # type: ignore[union-attr]
                "hash": hash if hash is not None else shown.hash,  # type: ignore[union-attr]
                "comment": comment,
                "role": role,
            }
        )
        return await self.send(event, who=who)

    async def run_job(self) -> bool:
        ran = await self.kit.service.run_next_job()
        await self.refresh()  # job events arrive outside any request
        return ran

    # -- reading ---------------------------------------------------------------------------
    async def snapshot(self) -> SessionSnapshot:
        return await self.kit.service.get_snapshot(self.who, self.session_id)

    def last_gate(self, gate: GateKind | None = None) -> GateEvent | None:
        for ev in reversed(self.events):
            if isinstance(ev, GateEvent) and (gate is None or ev.gate == gate):
                return ev
        return None

    def of_type(self, kind: type[E]) -> list[E]:
        return [e for e in self.events if isinstance(e, kind)]

    # -- shortcuts through the happy path ----------------------------------------------------
    async def to_gate1(self) -> GateEvent:
        """Talk until the Brief is on screen (an idea, then one tap per pending card)."""
        await self.say("a fitness tracking app")
        for _ in range(10):
            snap = await self.snapshot()
            if snap.state == "BRIEF_REVIEW":
                break
            await self.tap("A")
        gate = self.last_gate("brief")
        assert gate is not None
        return gate

    async def approve_gate1(self) -> list[OutboundEvent]:
        return await self.decide("brief", "approve")

    async def to_gate2(self) -> GateEvent:
        """Gate 1 approved, the plan job run to completion."""
        await self.to_gate1()
        await self.approve_gate1()
        assert await self.run_job()
        gate = self.last_gate("plan")
        assert gate is not None
        return gate

    async def approve_gate2(self) -> list[OutboundEvent]:
        return await self.decide("plan", "approve")

    async def refresh(self) -> None:
        self.events = list(await self.kit.service.list_events(self.who, self.session_id))
