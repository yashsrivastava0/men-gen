"""Random whole sessions for property tests: a list of user actions played against the real planner engine.

``Act`` is one thing a person can do (answer a card in words, tap, skip, say "you decide", edit an earlier answer,
pivot to a new idea, make a remark). ``play`` runs a list of them on a fresh planner service (mock providers,
in-memory store, fake clock), then ``finish`` answers whatever is left until the Brief is in review. The service,
the conversation and a snapshot of the fact ledger after every action are returned, so a property can check an
invariant at every step and at the end.

Two small understander wrappers stand in for what the offline reader cannot say: a *pivot* ("Actually, it is for a
clinic") and a *private remark* whose paraphrase is a fact but whose quote is the person's own words (the canary
the consumer-view property looks for).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from mde_compass.domain import Card
from mde_compass.domain.events import AnswerEdit
from mde_compass.domain.interview import Fact, FactEffect
from mde_compass.engine.ports import ExtractedFact, TurnUnderstanding
from mde_compass.scenarios import ledger
from mde_compass.scenarios.runner import new_service
from mde_compass.testing import Conversation

THOROUGH = os.environ.get("COMPASS_PROPERTIES") == "thorough"
SESSION_EXAMPLES = 60 if THOROUGH else 8  # a whole session per example: seconds, not milliseconds
PURE_EXAMPLES = 1000 if THOROUGH else 150

Kind = Literal["answer", "tap", "skip", "you_decide", "edit", "pivot", "remark"]
IDEAS: tuple[str, ...] = (
    "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch.",
    "I want an appointment booking app for my family clinic in Indore.",
    "A B2B inventory tool for pharmacies that tracks stock and expiry dates.",
    "mujhe pune me apni tiffin delivery service ke liye app chahiye",
)
PIVOT_TEXT = "Actually, it is for a clinic that books appointments"
CANARY_PREFIX = "zqk"
NEUTRAL: dict[str, str] = {
    "context.geo": "In Pune, India",
    "locales": "English and Hindi",
    "audience.segments": "Regular customers who order from their phone every day",
}
FILLER = (
    "We handle about 200 orders a day for regular customers and our own staff take care of them"
)


@dataclass(frozen=True)
class Act:
    kind: Kind
    text: str = ""
    pick: int = 0


@dataclass
class Played:
    conv: Conversation
    service: Any
    errors: list[str] = field(default_factory=list)
    snapshots: list[dict[str, Fact]] = field(
        default_factory=list
    )  # the fact ledger after every action

    async def data(self) -> ledger.SessionData:
        return await ledger.load_session(self.conv)


class Reader:
    """Wraps the service's understander: the pivot and the private remark need the model's judgement."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def understand(self, ctx: Any, text: str, **kw: Any) -> TurnUnderstanding:
        if text.startswith("Actually"):
            return TurnUnderstanding(
                intent="answer",
                flags=["pivot"],
                facts=[
                    ExtractedFact(
                        type="context",
                        statement="It is an appointment app for a clinic",
                        quote="it is for a clinic",
                        topics=["idea"],
                        aspects=["idea.summary:*"],
                        effects=[
                            FactEffect(
                                slot_id="idea.summary",
                                value_json='"An appointment app for a clinic"',
                            )
                        ],
                    )
                ],
            )
        if CANARY_PREFIX in text:
            start = text.index(CANARY_PREFIX)
            quote = text[start : start + 24].split()[0]
            return TurnUnderstanding(
                intent="answer",
                facts=[
                    ExtractedFact(
                        type="context",
                        statement="The person shared a private remark about the project",
                        quote=quote,
                        topics=["remarks"],
                        consumers=["all"],
                    )
                ],
            )
        return await self.inner.understand(ctx, text, **kw)


def remark(token: str) -> str:
    """A private remark that carries the canary token."""
    return f"Between us, please remember {CANARY_PREFIX}{token} when you write this up"


async def _snapshot(played: Played) -> None:
    data = await played.data()
    played.snapshots.append({f.id: f for f in data.facts})


async def _card_to_answer(conv: Conversation) -> Card | None:
    return conv.last_card()


async def _act(played: Played, act: Act) -> None:
    conv = played.conv
    card = conv.last_card()
    if act.kind == "remark":
        await conv.say(act.text or remark("abc"))
    elif act.kind == "pivot":
        await conv.say(PIVOT_TEXT)
    elif card is None:
        return
    elif act.kind == "tap":
        await conv.tap_recommended()
    elif act.kind == "skip":
        await conv.skip()
    elif act.kind == "you_decide":
        await conv.you_decide()
    elif act.kind == "answer":
        if card.options:
            opt = card.options[act.pick % len(card.options)]
            await conv.answer([opt.id] if card.type == "pick_many" else opt.id, card=card)
        else:
            await conv.answer(None, custom=act.text or FILLER, card=card)
    elif act.kind == "edit":
        data = await played.data()
        answered = [q for q in data.qa if q.status == "answered" and q.card.frame == "planned"]
        if not answered:
            return
        target = answered[act.pick % len(answered)]
        await conv.send(
            AnswerEdit(
                event_id=f"evt_edit_{len(played.snapshots):03d}",
                card_id=target.card_id,
                value=None,
                custom_text=act.text or "Students and hostel residents near the college",
            )
        )


async def finish(played: Played, limit: int = 60) -> str:
    """Answer every remaining card in a neutral way until the Brief is in review (or the limit is hit)."""
    conv = played.conv
    for _ in range(limit):
        state = await conv.state()
        card = conv.last_card()
        if (
            state in ("BRIEF_REVIEW", "APPROVED")
            or card is None
            or state
            not in (
                "INTERVIEWING",
                "PLAYBACK",
                "UNDERSTANDING",
            )
        ):
            return state
        if card.options:
            await conv.tap_recommended()
        else:
            text = next((NEUTRAL[n] for n in card.need_ids if n in NEUTRAL), FILLER)
            await conv.answer(None, custom=text, card=card)
        _record_errors(played)
    return await conv.state()


def _record_errors(played: Played) -> None:
    seen = len(played.errors)
    errs = [
        f"{e.payload.get('code')}: {e.payload.get('message')}"
        for e in played.conv.events
        if e.type == "error"
    ]
    played.errors = errs if len(errs) > seen else played.errors


async def play(acts: list[Act], *, idea: str = IDEAS[0], complete: bool = True) -> Played:
    """Run the actions, then (by default) finish the interview so the Brief is compiled."""
    service = new_service("planner")
    assert service.ports is not None
    object.__setattr__(service.ports, "understander", Reader(service.ports.understander))
    conv = await Conversation.start(service)
    played = Played(conv, service)
    await conv.say(idea)
    _record_errors(played)
    await _snapshot(played)
    for act in acts:
        await _act(played, act)
        _record_errors(played)
        await _snapshot(played)
    if complete:
        await finish(played)
        await _snapshot(played)
    return played
