"""Scripted conversations from "hi" to ``spec_ready`` (one per archetype and policy profile).

A journey talks to a :class:`Driver`, so the same script runs over ``FlowDriver`` today and over the
HTTP driver once ``apps/context-api`` exists. Guided sessions tap once per gate; Enterprise sessions
let every required approver role approve the SAME (version, hash).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from evals.harness.driver import Driver, DriverError
from mde_contracts.common import GateKind, Mode
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateEvent,
    Notice,
    OutboundEvent,
    SpecReady,
)
from mde_contracts.gates import ApprovalRecord
from mde_contracts.records import SpecVersionRecord

#: how many builder questions (needs_answer) a scripted journey answers before it gives up
MAX_BUILDER_QUESTIONS = 2

ARCHETYPES: tuple[str, ...] = (
    "commerce",
    "food_delivery",
    "booking_services",
    "internal_workforce",
    "custom",
)


@dataclass(frozen=True)
class Journey:
    name: str
    archetype: str  # the archetype the opening message must be recognised as
    opening: str
    mode: Mode = "guided"
    policy_id: str | None = None
    language: str = "en"


_OPENINGS: dict[str, str] = {
    "commerce": "An online shop for handmade candles",
    "food_delivery": "I run a restaurant and want customers to order food for delivery",
    "booking_services": "A booking app for my hair salon so clients can book appointments",
    "internal_workforce": (
        "A company app for our employees and field staff: checklists, attendance and approvals"
    ),
    "custom": "A community garden tracker where neighbours share plots and tools",
}

GUIDED: tuple[Journey, ...] = tuple(Journey(f"{a}-guided", a, _OPENINGS[a]) for a in ARCHETYPES)

FIRM = "enterprise-it-services-firm"
BANK = "regulated-bank"
ENTERPRISE: tuple[Journey, ...] = (
    Journey("commerce-firm", "commerce", _OPENINGS["commerce"], "enterprise", FIRM),
    Journey(
        "internal_workforce-firm",
        "internal_workforce",
        _OPENINGS["internal_workforce"],
        "enterprise",
        FIRM,
    ),
    Journey(
        "booking_services-bank",
        "booking_services",
        _OPENINGS["booking_services"],
        "enterprise",
        BANK,
    ),
    Journey("custom-bank", "custom", _OPENINGS["custom"], "enterprise", BANK),
)

LANGUAGES: tuple[Journey, ...] = (
    Journey("hindi", "commerce", "मुझे अपनी दुकान के लिए ऐप चाहिए", language="hi"),
    Journey(
        "hinglish",
        "booking_services",
        "mujhe salon ka app chahiye, booking aur reminders ke saath",
        language="hi-Latn",
    ),
)


@dataclass
class JourneyResult:
    journey: Journey
    session_id: str
    tenant_id: str
    app_id: str
    events: list[OutboundEvent]
    approvals: list[ApprovalRecord]
    specs: list[SpecVersionRecord]
    cards_asked: list[str] = field(default_factory=lambda: [])

    @property
    def spec(self) -> SpecVersionRecord:
        return self.specs[-1]

    def of_type(self, kind: type[OutboundEvent]) -> list[OutboundEvent]:
        return [e for e in self.events if isinstance(e, kind)]

    @property
    def spec_ready(self) -> list[SpecReady]:
        return [e for e in self.events if isinstance(e, SpecReady)]


async def approve_gate(driver: Driver, gate: GateKind) -> list[OutboundEvent]:
    """Every required role approves the gate currently shown (the same version and hash)."""
    shown = [e for e in await driver.events() if isinstance(e, GateEvent) and e.gate == gate]
    if not shown:
        raise DriverError(f"no {gate} gate is open")
    out: list[OutboundEvent] = []
    for role in shown[-1].required_roles or ["requester"]:
        out += await driver.decide(
            gate,
            "approve",
            role=role,
            as_user=None if role == "requester" else f"u-{role}",
        )
    return out


async def reach_brief_gate(driver: Driver, journey: Journey, *, hi: bool = True) -> None:
    """ "hi", the opening idea, then the recommended answer to every card: Gate 1 is open."""
    await driver.start(mode=journey.mode, policy_id=journey.policy_id)
    if hi:
        await driver.say("hi")
    await driver.say(journey.opening)
    await driver.answer_with_recommended()
    snap = await driver.snapshot()
    if snap.state != "BRIEF_REVIEW":
        raise DriverError(f"{journey.name}: the interview ended in {snap.state}, not BRIEF_REVIEW")


async def reach_plan_gate(driver: Driver, journey: Journey, *, hi: bool = True) -> None:
    """Through Gate 1 and the plan run: Gate 2 is open."""
    await reach_brief_gate(driver, journey, hi=hi)
    await approve_gate(driver, "brief")
    if not await driver.run_plan():
        raise DriverError(f"{journey.name}: no plan job was queued after Gate 1")
    snap = await driver.snapshot()
    # When repair cannot remove an error the builder asks ONE plain question and the session goes
    # back to Discover (needs_answer, SIT-034). A user answers it and the plan runs again, without
    # redoing the research. The mock never gets here; a real model did (D-083).
    for _ in range(MAX_BUILDER_QUESTIONS):
        if snap.state != "DISCOVERING" or snap.pending_card is None:
            break
        await driver.answer_with_recommended()
        if not await driver.run_plan():
            raise DriverError(f"{journey.name}: no plan job was queued after the answer")
        snap = await driver.snapshot()
    if snap.state != "PLAN_REVIEW":
        raise DriverError(f"{journey.name}: planning ended in {snap.state}, not PLAN_REVIEW")


async def collect(driver: Driver, journey: Journey) -> JourneyResult:
    events = await driver.events()
    record = await driver.session_record()
    return JourneyResult(
        journey=journey,
        session_id=driver.session_id,
        tenant_id=record.tenant_id,
        app_id=record.app_id,
        events=events,
        approvals=await driver.approvals(),
        specs=await driver.specs(),
        cards_asked=[e.card.card.id for e in events if isinstance(e, CardEvent)],
    )


async def play_journey(driver: Driver, journey: Journey, *, hi: bool = True) -> JourneyResult:
    """ "hi" -> the opening idea -> recommended answers -> Gate 1 -> plan -> Gate 2 -> spec_ready."""
    await reach_plan_gate(driver, journey, hi=hi)
    await approve_gate(driver, "plan")
    return await collect(driver, journey)


def texts(events: Sequence[OutboundEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, AssistantMessage | Notice)]
