"""The research plan: places, targets (topics) and what the worthiness gate dropped.

A ``Target`` is one topic worth searching: a Spec decision it feeds (``spec_need`` and ``lane``), the words
a query may be built from (``anchors``), where (``Place``), what must be found (``slots``) and which Brief ids
it serves (``links``). Targets are built by CODE from the Brief and the job templates in ``data/jobs``; a model
may add or reword some (RSCH-1) but never writes a query that skips the anatomy check.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_research_agent.catalog.models import SlotSpec
from mde_research_agent.domain.input import PlanSource

Origin = Literal["template", "request", "fact", "model", "discovery"]
DropReason = Literal["declined", "trivia", "own_brand", "not_worthwhile", "over_budget"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Place(Frozen):
    """Where to search. ``phrases`` are what a query may name (the country plus every stated city when the
    geography is uncertain); ``unknown`` means nothing was stated and no place is required in queries."""

    phrases: tuple[str, ...] = ()
    country_code: str | None = None
    country_name: str | None = None
    languages: tuple[str, ...] = ()  # search languages other than English
    unknown: bool = False
    uncertain: bool = False
    inferred: bool = False  # the country came from a model guess, not from the Brief

    @property
    def text(self) -> str:
        """The place words for a query: the first two phrases."""
        return " ".join(self.phrases[:2])


class Links(Frozen):
    requests: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    flows: tuple[str, ...] = ()
    integrations: tuple[str, ...] = ()


class Target(Frozen):
    id: str
    job: str
    template_id: str
    spec_need: str
    lane: str
    also_lanes: tuple[str, ...] = ()
    label: str
    question: str
    decision_unlocked: str
    anchors: tuple[str, ...]
    entity: str | None = None  # a named entity (a reference, a provider need, a discovered company)
    place: Place | None = None  # None: global (platform guidelines)
    intent: tuple[str, ...]
    needs: tuple[str, ...]
    priority: int = 50
    slots: tuple[SlotSpec, ...] = ()
    links: Links = Links()
    flags: tuple[str, ...] = ()
    recency_months: int | None = None
    origin: Origin = "template"
    terms: tuple[str, ...] = ()  # words for passage selection
    domain_restrict: tuple[str, ...] = ()  # read this entity's own site (entity deepening)
    seed_names: tuple[str, ...] = ()  # entity names from model memory: discovery queries only


class Dropped(Frozen):
    label: str
    reason: DropReason
    job: str = ""
    links: Links = Links()


class Plan(Frozen):
    targets: tuple[Target, ...]
    dropped: tuple[Dropped, ...] = ()
    unrun: tuple[Target, ...] = ()  # worthy but over the ceiling: visible gaps, never silent
    place: Place
    category_phrase: str
    discovery_names: tuple[str, ...] = ()
    plan_source: PlanSource = "from_request"
    notes: tuple[str, ...] = ()
    light: bool = (
        False  # a tiny app: only the feature norms and the screen inventory are researched
    )
    signals: tuple[str, ...] = ()  # capability signals found in the Brief (doc 13 4.1)
