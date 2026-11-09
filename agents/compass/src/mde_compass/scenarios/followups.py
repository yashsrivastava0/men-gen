"""The follow-up rate battery (build prompt 02, section 6): a follow-up must fire on at least 90% of scripted vague
answers to a high-impact need and on none of the specific ones.

Each case is a fresh session on the planner engine (mock providers, in-memory store, fake clock): the idea is
said, the first planned question is answered with a scripted vague or specific answer, and the case records
whether the NEXT question shown is a follow-up of that very question. The vague / specific rule the mock
understander applies is documented in ``calls/understand_turn/facts.py``; a live run replaces it with the model's
judgement and the same battery measures that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mde_compass.domain import Card
from mde_compass.scenarios.runner import new_service
from mde_compass.testing import Conversation

IDEAS: tuple[str, ...] = (
    "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch.",
    "I want an appointment booking app for my family clinic in Nagpur.",
    "I need a B2B inventory tool for pharmacies that reorders stock from distributors.",
    "I want a language learning app for children aged six to ten.",
    "I want a ride sharing app for senior citizens in Bengaluru.",
)
VAGUE: tuple[str, ...] = ("office workers", "people", "everyone", "normal users", "customers")
SPECIFIC: tuple[str, ...] = (
    "Office workers in Baner and Hinjewadi who order lunch every weekday before 11 am",
    "Parents of young children, who book a visit for the whole family, and elderly patients",
    "Pharmacy owners and their store staff who check stock every morning and reorder weekly",
    "Children aged six to ten who practise ten minutes a day, and parents who track progress",
    "Senior citizens living alone who need a safe ride to the doctor, and their children abroad",
)


@dataclass(frozen=True)
class Case:
    idea: str
    answer: str
    asked: str  # id of the question that was answered
    need_ids: tuple[str, ...]
    fired: bool  # the next question shown is a follow-up of it


async def run_case(idea: str, answer: str, engine: str = "planner") -> Case:
    conv = await Conversation.start(new_service(engine))  # type: ignore[arg-type]
    await conv.say(idea)
    first = conv.last_card()
    if first is None:
        raise AssertionError("the planner path showed no first question")
    await conv.answer(None, custom=answer, card=first)
    nxt: Card | None = conv.last_card()
    fired = nxt is not None and nxt.origin == "followup" and nxt.parent_card_id == first.id
    return Case(idea, answer, first.id, tuple(first.need_ids), fired)


async def measure_followups(engine: str = "planner") -> dict[str, Any]:
    """Run the battery; returns the rates and every case (for the report)."""
    vague = [await run_case(i, a, engine) for i, a in zip(IDEAS, VAGUE, strict=True)]
    specific = [await run_case(i, a, engine) for i, a in zip(IDEAS, SPECIFIC, strict=True)]

    def summary(cases: list[Case]) -> dict[str, Any]:
        fired = sum(1 for c in cases if c.fired)
        return {
            "cases": len(cases),
            "fired": fired,
            "rate": round(fired / len(cases), 4) if cases else None,
            "detail": [
                {
                    "idea": c.idea[:48],
                    "answer": c.answer[:48],
                    "need_ids": list(c.need_ids),
                    "fired": c.fired,
                }
                for c in cases
            ],
        }

    return {"vague": summary(vague), "specific": summary(specific)}
