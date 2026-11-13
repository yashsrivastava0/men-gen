"""What a model may suggest to the planner (RSCH-1). Plain data; the planner validates every field."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposedTarget:
    job: str
    anchor: str
    question: str
    decision_unlocked: str
    needs: tuple[str, ...] = ()
    priority: int = 50
    trivia: bool = False
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanProposal:
    country_code: str | None = None
    country_name: str | None = None
    cities: tuple[str, ...] = ()
    category_phrase: str = ""
    extra_targets: tuple[ProposedTarget, ...] = ()
    discovery_names: tuple[str, ...] = ()
