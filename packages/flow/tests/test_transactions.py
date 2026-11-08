"""Transaction boundaries: no unit of work (database connection) is held across an engine call.

A unit of work pins one pooled connection, and an engine (LLM) call can take tens of seconds, so the
flow runs its turns as transactions *replayed around* the engine calls (``FlowService._run_turn``):
the engine runs with nothing held, its result is journalled, and the turn logic runs again from the
stored state. These tests prove that with a pool of two connections and six concurrent sessions
each stuck in a slow engine, and pin the rest of the contract of that design: idempotent events (also
while one is in flight and across processes), compare-and-set retries, a state that moved during an
engine call, and a failed turn leaving nothing behind.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mde_contracts.engines import (
    BuildRequest,
    BuildResult,
    DiscoveryRequest,
    DiscoveryResult,
    ResearchRequest,
    RunContext,
)
from mde_contracts.events import (
    AssistantMessage,
    InboundEvent,
    Notice,
    SpecReady,
    UserMessage,
)
from mde_contracts.ports import SessionRepo, Store, UnitOfWork
from mde_contracts.records import JobRecord, SessionRecord
from mde_contracts.research import ResearchNote
from mde_core.cancel import CancellationToken
from mde_core.clock import Clock
from mde_core.errors import Conflict
from mde_flow import messages
from mde_flow.service import FlowService
from mde_flow.testing.fakes import FakeDiscoveryEngine, FakeResearchEngine, FakeSpecBuilder
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit, principal
from mde_flow.testing.store import InMemoryStore

SESSIONS = 6  # more concurrent engine calls than the pool of two has connections
POOL = 2
GUARD = 20.0  # seconds: only ever reached when a test is already failing (a held connection)


class Rendezvous:
    """``expect`` callers get inside; the test looks around; ``release()`` lets them all go."""

    def __init__(self, expect: int = 1) -> None:
        self.expect = expect
        self.inside = 0
        self.entered = 0
        self.all_inside = asyncio.Event()
        self._released = asyncio.Event()

    async def pass_through(self) -> None:
        self.inside += 1
        self.entered += 1
        if self.inside >= self.expect:
            self.all_inside.set()
        try:
            await self._released.wait()
        finally:
            self.inside -= 1

    async def everyone_inside(self) -> None:
        await asyncio.wait_for(self.all_inside.wait(), GUARD)

    def release(self) -> None:
        self._released.set()


@dataclass
class SlowDiscovery(FakeDiscoveryEngine):
    """The scripted Discover engine, but ``handle`` waits at a rendezvous (an LLM call)."""

    rendezvous: Rendezvous = field(default_factory=Rendezvous)
    on_inside: Callable[[], Awaitable[None]] | None = None
    boom: bool = False
    seen: list[DiscoveryRequest] = field(default_factory=list[DiscoveryRequest])

    async def handle(
        self, req: DiscoveryRequest, event: InboundEvent, ctx: RunContext
    ) -> DiscoveryResult:
        self.seen.append(req)
        if self.on_inside is not None:
            hook, self.on_inside = self.on_inside, None  # once
            await hook()
        await self.rendezvous.pass_through()
        if self.boom:
            self.boom = False
            raise RuntimeError("the model fell over")
        return await super().handle(req, event, ctx)


class SlowResearch(FakeResearchEngine):
    def __init__(self, rendezvous: Rendezvous) -> None:
        super().__init__()
        self.rendezvous = rendezvous

    async def run(self, req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        await self.rendezvous.pass_through()
        return await super().run(req, ctx)


class SlowBuilder(FakeSpecBuilder):
    def __init__(self, rendezvous: Rendezvous, kit: FlowKit) -> None:
        super().__init__(ids=kit.ids)
        self.rendezvous = rendezvous

    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult:
        await self.rendezvous.pass_through()
        return await super().build(req, ctx)


def slow_kit(
    expect: int = 1, store_factory: Callable[[Clock, Callable[[str], str]], Store] | None = None
) -> tuple[FlowKit, SlowDiscovery]:
    kit = make_flow_kit(store_factory=store_factory)
    slow = SlowDiscovery(ids=kit.ids, rendezvous=Rendezvous(expect))
    kit.service.discovery = slow
    return kit, slow


async def several_drivers(kit: FlowKit, n: int) -> list[Driver]:
    return [await kit.driver(principal=principal(user_id=f"user-{i}")) for i in range(n)]


@pytest.fixture
async def small_pool(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    """A real Postgres engine with a pool of TWO connections and no overflow."""
    from mde_persistence.db import ensure_selector_loop_policy, normalize_url
    from mde_persistence.testing_plugin import truncate_all

    database = request.getfixturevalue("pg_database")
    truncate_all(database)
    ensure_selector_loop_policy()
    engine = create_async_engine(
        normalize_url(database), pool_size=POOL, max_overflow=0, pool_timeout=GUARD
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def checked_out(engine: AsyncEngine) -> int:
    return engine.pool.checkedout()  # type: ignore[attr-defined, no-any-return]


# ----------------------------------------------------------------------------- the pool of two


@pytest.mark.integration
async def test_postgres_pool_of_two_serves_six_sessions_stuck_in_a_slow_discover_engine(
    small_pool: AsyncEngine,
) -> None:
    from mde_persistence.store import PostgresStore

    kit, slow = slow_kit(SESSIONS, lambda clock, ids: PostgresStore(small_pool, clock=clock))
    drivers = await several_drivers(kit, SESSIONS)
    turns = [asyncio.create_task(d.say("a fitness tracking app")) for d in drivers]
    await slow.rendezvous.everyone_inside()
    # six engine calls are in flight at once; a held connection would have stopped the third
    assert slow.rendezvous.inside == SESSIONS and checked_out(small_pool) == 0
    slow.rendezvous.release()
    replies = await asyncio.wait_for(asyncio.gather(*turns), GUARD)
    assert all(any(isinstance(e, AssistantMessage) for e in r) for r in replies)
    assert checked_out(small_pool) == 0
    assert {(await d.snapshot()).state for d in drivers} == {"DISCOVERING"}


async def test_in_memory_units_of_work_are_not_open_during_engine_calls() -> None:
    kit, slow = slow_kit(SESSIONS)
    store = kit.memory
    drivers = await several_drivers(kit, SESSIONS)
    turns = [asyncio.create_task(d.say("a fitness tracking app")) for d in drivers]
    await slow.rendezvous.everyone_inside()
    assert store.open_units == 0  # nothing is open while all six engines are running
    slow.rendezvous.release()
    await asyncio.gather(*turns)
    assert store.open_units == 0 and store.peak_open_units <= SESSIONS


@pytest.mark.integration
async def test_postgres_pool_of_two_serves_six_plan_jobs_stuck_in_research_then_build(
    small_pool: AsyncEngine,
) -> None:
    from mde_persistence.store import PostgresStore

    kit = make_flow_kit(store_factory=lambda clock, ids: PostgresStore(small_pool, clock=clock))
    drivers = await several_drivers(kit, SESSIONS)
    for d in drivers:  # six approved Briefs: six queued runs
        await d.to_gate1()
        await d.approve_gate1()
    in_research, in_build = Rendezvous(SESSIONS), Rendezvous(SESSIONS)
    kit.service.research = SlowResearch(in_research)
    kit.service.builder = SlowBuilder(in_build, kit)
    jobs = [asyncio.create_task(kit.service.run_next_job(f"worker-{i}")) for i in range(SESSIONS)]
    await in_research.everyone_inside()
    assert in_research.inside == SESSIONS and checked_out(small_pool) == 0
    in_research.release()
    await in_build.everyone_inside()
    assert in_build.inside == SESSIONS and checked_out(small_pool) == 0
    in_build.release()
    assert all(await asyncio.wait_for(asyncio.gather(*jobs), GUARD))
    assert {(await d.snapshot()).state for d in drivers} == {"PLAN_REVIEW"}
    assert checked_out(small_pool) == 0


async def test_in_memory_plan_jobs_hold_no_unit_of_work_inside_the_engines() -> None:
    kit = make_flow_kit()
    drivers = await several_drivers(kit, SESSIONS)
    for d in drivers:
        await d.to_gate1()
        await d.approve_gate1()
    in_research, in_build = Rendezvous(SESSIONS), Rendezvous(SESSIONS)
    kit.service.research = SlowResearch(in_research)
    kit.service.builder = SlowBuilder(in_build, kit)
    jobs = [asyncio.create_task(kit.service.run_next_job(f"worker-{i}")) for i in range(SESSIONS)]
    await in_research.everyone_inside()
    assert kit.memory.open_units == 0
    in_research.release()
    await in_build.everyone_inside()
    assert kit.memory.open_units == 0
    in_build.release()
    await asyncio.gather(*jobs)
    assert {(await d.snapshot()).state for d in drivers} == {"PLAN_REVIEW"}


async def test_a_queue_drain_at_plan_ready_runs_its_engine_calls_outside_a_unit_of_work() -> None:
    """The queued messages replayed at Gate 2 are engine calls too (the job's last transaction)."""
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    hold = kit.research.pause_inside()
    job = asyncio.create_task(d.run_job())
    await hold.entered.wait()
    for note in ("set design.direction=bold-dark", "feature+ chat"):
        await d.say(note)  # queued while PLANNING
    seen_open: list[int] = []
    original = kit.discovery.handle

    async def watching(
        req: DiscoveryRequest, event: InboundEvent, ctx: RunContext
    ) -> DiscoveryResult:
        seen_open.append(kit.memory.open_units)
        return await original(req, event, ctx)

    kit.discovery.handle = watching  # type: ignore[method-assign]
    hold.release()
    await job
    assert seen_open and set(seen_open) == {0}
    assert (await d.snapshot()).state == "PLANNING"  # the queued notes became a change run


# ----------------------------------------------------------------------------- the pass count


async def test_a_turn_is_one_pass_per_engine_call_plus_the_committing_pass() -> None:
    kit, slow = slow_kit()
    slow.rendezvous.release()
    d = await kit.driver()
    before = kit.memory.begin_count
    await d.say("a fitness tracking app")  # one discovery.handle call
    assert kit.memory.begin_count - before == 2
    assert len(slow.seen) == 1


# ----------------------------------------------------------------------------- duplicate events


async def test_a_duplicate_of_an_event_in_flight_is_answered_at_once_without_a_second_effect(
    store_matrix: None,
) -> None:
    kit, slow = slow_kit()
    d = await kit.driver()
    event = UserMessage(event_id=d.eid(), text="a fitness tracking app")
    first = asyncio.create_task(kit.service.handle_event(d.who, d.session_id, event))
    await slow.rendezvous.everyone_inside()
    # the same event again while the engine is still working: an immediate in-progress notice
    dup = await asyncio.wait_for(kit.service.handle_event(d.who, d.session_id, event), GUARD)
    assert [type(e) for e in dup] == [Notice]
    assert isinstance(dup[0], Notice) and dup[0].code == messages.N_IN_PROGRESS
    assert slow.rendezvous.entered == 1  # the engine was not asked again
    before = len(await kit.peek.outbound(d.session_id))
    slow.rendezvous.release()
    replies = await asyncio.wait_for(first, GUARD)
    assert replies and all(e.in_reply_to == event.event_id for e in replies)
    # and once it is done, the stored replies come back, again without a second effect
    again = await kit.service.handle_event(d.who, d.session_id, event)
    assert [e.event_id for e in again] == [e.event_id for e in replies]
    assert slow.rendezvous.entered == 1
    assert len(await kit.peek.outbound(d.session_id)) == before + len(replies)


async def test_the_same_event_delivered_to_two_processes_has_one_effect(
    store_matrix: None,
) -> None:
    """Two services over one store (two processes): both run the engine, one commits, the other
    finds the event already recorded at the idempotent insert and returns the stored replies."""
    kit, slow = slow_kit(2)
    other = FlowService(
        store=kit.store,
        discovery=slow,
        research=kit.research,
        spec_builder=kit.builder,
        project_repo=kit.repo,
        frontend=kit.frontend,
        policies=kit.policies,
        clock=kit.clock,
        ids=kit.ids,
    )
    d = await kit.driver()
    event = UserMessage(event_id=d.eid(), text="a fitness tracking app")
    one = asyncio.create_task(kit.service.handle_event(d.who, d.session_id, event))
    two = asyncio.create_task(other.handle_event(d.who, d.session_id, event))
    await slow.rendezvous.everyone_inside()
    slow.rendezvous.release()
    a, b = await asyncio.wait_for(asyncio.gather(one, two), GUARD)
    assert [e.event_id for e in a] == [e.event_id for e in b]  # the same replies for both callers
    stored = [e for e in await kit.peek.outbound(d.session_id) if e.in_reply_to == event.event_id]
    assert [e.event_id for e in stored] == [e.event_id for e in a]
    snapshot = await d.snapshot()
    assert snapshot.slots["idea.summary"].value == "a fitness tracking app"
    assert snapshot.pending_card is not None  # asked once, not twice


# ----------------------------------------------------------------------------- failures and races


async def test_a_failing_engine_leaves_nothing_behind_and_the_same_event_can_be_retried(
    store_matrix: None,
) -> None:
    kit, slow = slow_kit()
    slow.rendezvous.release()
    d = await kit.driver()
    stored_before = await kit.peek.outbound(d.session_id)
    slow.boom = True
    event = UserMessage(event_id=d.eid(), text="a fitness tracking app")
    with pytest.raises(RuntimeError):
        await kit.service.handle_event(d.who, d.session_id, event)
    assert await kit.peek.outbound(d.session_id) == stored_before  # no half-recorded event
    assert (await d.snapshot()).slots == {}
    replies = await kit.service.handle_event(d.who, d.session_id, event)  # the retry is processed
    assert replies and (await d.snapshot()).slots["idea.summary"].value == "a fitness tracking app"


async def test_state_that_moves_during_an_engine_call_makes_the_turn_ask_the_engine_again(
    store_matrix: None,
) -> None:
    """A rival writer (another process) changes the session while the engine is thinking. The
    journalled result was computed for a state that no longer exists, so it is not used."""
    kit, slow = slow_kit()
    d = await kit.driver()

    async def rival_writes() -> None:
        async with kit.store.begin(d.who.tenant_id) as uow:
            rec = await uow.sessions.get(d.session_id, for_update=True)
            assert rec is not None
            moved = rec.discovery.model_copy(update={"turn_count": 5})
            await uow.sessions.save(
                rec.model_copy(update={"discovery": moved}), expected_version=rec.version
            )

    event = UserMessage(event_id=d.eid(), text="a fitness tracking app")
    turn = asyncio.create_task(kit.service.handle_event(d.who, d.session_id, event))
    await slow.rendezvous.everyone_inside()
    await rival_writes()
    slow.rendezvous.release()
    replies = await asyncio.wait_for(turn, GUARD)
    assert replies
    assert [r.state.turn_count for r in slow.seen] == [0, 5]  # asked again, with the new state
    stored = await kit.peek.session(d.session_id)
    assert stored.discovery.turn_count == 6  # the engine's answer was built on the rival's state
    assert stored.discovery.slots["idea.summary"].value is not None
    sent = [e for e in await kit.peek.outbound(d.session_id) if e.in_reply_to == event.event_id]
    assert [e.event_id for e in sent] == [e.event_id for e in replies]  # one effect


class _RivalSessions:
    """``SessionRepo`` without row locks, whose first ``save`` is beaten by a rival write."""

    def __init__(self, inner: SessionRepo, store: _RivalStore, tenant_id: str | None) -> None:
        self._inner = inner
        self._store = store
        self._tenant_id = tenant_id

    async def create(self, rec: SessionRecord) -> SessionRecord:
        return await self._inner.create(rec)

    async def get(self, session_id: str, *, for_update: bool = False) -> SessionRecord | None:
        return await self._inner.get(session_id)  # no row lock

    async def save(self, rec: SessionRecord, *, expected_version: int) -> SessionRecord:
        if self._store.armed:
            self._store.armed = False
            self._store.rival_writes += 1
            async with self._store.inner.begin(self._tenant_id) as other:
                current = await other.sessions.get(rec.id)
                assert current is not None
                await other.sessions.save(current, expected_version=current.version)
        return await self._inner.save(rec, expected_version=expected_version)

    async def list_for_owner(self, owner_id: str, *, limit: int = 50) -> list[SessionRecord]:
        return await self._inner.list_for_owner(owner_id, limit=limit)


class _RivalStore:
    """Wraps a store so a compare-and-set is lost deterministically (a writer that did not lock)."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.rival_writes = 0
        self.armed = False

    @asynccontextmanager
    async def begin(self, tenant_id: str | None) -> AsyncIterator[UnitOfWork]:
        async with self.inner.begin(tenant_id) as uow:
            uow.sessions = _RivalSessions(uow.sessions, self, tenant_id)
            yield uow


async def test_a_lost_compare_and_set_rereads_and_retries_without_double_applying() -> None:
    rival: list[_RivalStore] = []

    def factory(clock: Clock, ids: Callable[[str], str]) -> Store:
        rival.append(_RivalStore(InMemoryStore(clock=clock, ids=ids)))
        return rival[0]

    kit = make_flow_kit(store_factory=factory)
    d = await kit.driver()
    rival[0].armed = True  # the next save is beaten by a rival write
    replies = await d.say("a fitness tracking app")
    assert rival[0].rival_writes == 1 and replies
    snapshot = await d.snapshot()
    assert snapshot.slots["idea.summary"].value == "a fitness tracking app"
    sent = [
        e for e in await kit.peek.outbound(d.session_id) if e.in_reply_to == d.events[-1].event_id
    ]
    assert not sent or [e.event_id for e in sent] == [e.event_id for e in replies]
    cards = [e for e in await kit.peek.outbound(d.session_id) if e.type == "card"]
    assert len(cards) == 1  # the question was asked once, not once per attempt


async def test_a_session_that_never_stops_changing_gives_up_with_a_conflict() -> None:
    rival: list[_RivalStore] = []

    class Relentless(_RivalStore):
        @property
        def armed(self) -> bool:  # type: ignore[override]
            return True

        @armed.setter
        def armed(self, value: bool) -> None:
            return None

    def factory(clock: Clock, ids: Callable[[str], str]) -> Store:
        rival.append(Relentless(InMemoryStore(clock=clock, ids=ids)))
        return rival[0]

    kit = make_flow_kit(store_factory=factory)
    d = await kit.driver()
    with pytest.raises(Conflict):
        await d.say("a fitness tracking app")
    assert (await d.snapshot()).slots == {}  # and nothing was applied
    assert not [e for e in d.events if isinstance(e, SpecReady)]


# ----------------------------------------------------------------------------- the real queue


@pytest.mark.integration
async def test_the_flow_runs_as_a_job_handler_of_the_persistence_worker(
    make_store: Callable[..., Store],
) -> None:
    """``run_plan_job`` has the shape of ``mde_persistence.worker.JobHandler``: the worker claims
    the job, the flow runs it (and ends it itself), the worker's own completion is then a no-op."""
    from mde_persistence.worker import InlineJobRunner

    kit = make_flow_kit(store_factory=lambda clock, ids: make_store(clock=clock))
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()

    async def handler(job: JobRecord, token: CancellationToken) -> None:
        await kit.service.run_plan_job(job, "inline", token)

    runner = InlineJobRunner(kit.store, handler)
    ended = await runner.run_next()
    assert ended is not None and ended.state == "succeeded"
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert await runner.run_next() is None  # nothing else queued, nothing requeued
