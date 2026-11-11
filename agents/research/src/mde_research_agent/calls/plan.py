"""RSCH-1: the planner call. Turns the Brief into a place, a clean category phrase and a few extra topics.

The deterministic planner (``mde_research_agent.plan``) already builds the plan from the Brief and the job
templates; this call only IMPROVES it: it infers the country when the Brief states none (flagged, never silently
trusted), cleans the category phrase, proposes up to a handful of extra topics the templates miss, marks
trivia (a currency symbol never changes a spec decision) and may name competitors or providers from memory as
discovery seeds (labelled ``entity_from_model``, never facts). Everything it returns is validated by code. The Brief
text is the user's, so it is wrapped as untrusted and the call has no tools. Mock handler and fallback: the
Brief's own values, no extras.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.countries import countries

CALL_ID = "rsch.plan"
JOBS = ("features", "structure", "flows", "data", "services", "design", "screens", "local")


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureIn(W):
    id: str
    name: str
    intent: str
    priority: str


class RequestIn(W):
    id: str
    topic: str
    question: str


class ExistingIn(W):
    job: str
    anchor: str


class PlanIn(W):
    language: str
    summary: str
    category: str
    subcategory: str
    business_model: str
    country: str
    country_code: str
    region: str
    cities: list[str]
    geo_uncertain: bool
    locales: list[str]
    features: list[FeatureIn]
    roles: list[str]
    entities: list[str]
    modules: list[str]
    facts: list[str]
    requests: list[RequestIn]
    existing: list[ExistingIn]
    declined: list[str]
    text: str = ""  # the whole Brief view as ONE untrusted block

    @model_validator(mode="after")
    def _build_text(self) -> PlanIn:
        lines = [
            f"Idea: {self.summary}",
            f"Category: {self.category} / {self.subcategory}; business model: {self.business_model}",
            f'Geography stated: country "{self.country}" ({self.country_code}), region "{self.region}", '
            f"cities: {', '.join(self.cities)}; weak geography: {self.geo_uncertain}",
            f"App languages: {', '.join(self.locales)}",
            "Features:",
            *[f"- {f.id} {f.name}: {f.intent} [{f.priority}]" for f in self.features],
            f"Roles: {'; '.join(self.roles)}",
            f"Entities: {'; '.join(self.entities)}",
            f"Modules: {'; '.join(self.modules)}",
            "Facts with rules, numbers, compliance or integration details:",
            *[f"- {f}" for f in self.facts],
            "Trusted research requests:",
            *[f"- {r.id}: {r.topic} | {r.question}" for r in self.requests],
            "Topics already planned (do not repeat):",
            *[f"- {e.job}: {e.anchor}" for e in self.existing],
            f"Declined by the person, never research: {'; '.join(self.declined)}",
        ]
        self.text = "\n".join(lines)
        return self


class PlaceOut(W):
    country_code: str | None = None
    country_name: str | None = None
    cities: list[str]


class ExtraOut(W):
    job: Literal["features", "structure", "flows", "data", "services", "design", "screens", "local"]
    anchor: str
    question: str
    decision_unlocked: str
    needs: list[Literal["current", "norm", "entity", "number"]]
    priority: int
    trivia: bool
    terms: list[str]


class PlanOut(W):
    place: PlaceOut
    category_phrase: str
    extra_targets: list[ExtraOut]
    discovery_names: list[str]


def deterministic_plan(inp: PlanIn) -> PlanOut:
    """The Brief's own values; no extras, no guesses."""
    cc = inp.country_code.lower() or None
    return PlanOut(
        place=PlaceOut(country_code=cc, country_name=inp.country or None, cities=list(inp.cities)),
        category_phrase=inp.category.replace("_", " ").strip(),
        extra_targets=[],
        discovery_names=[],
    )


def handle(inp: PlanIn) -> PlanOut:
    return deterministic_plan(inp)


def validate(inp: PlanIn, out: PlanOut) -> list[str]:
    errors: list[str] = []
    cc = out.place.country_code
    if cc is not None and (len(cc) != 2 or cc.lower() not in countries()):
        errors.append(f"unknown country code {cc!r}")
    if len(out.extra_targets) > 12:
        errors.append("too many extra targets")
    return errors


SPEC: ResearchCallSpec[PlanIn, PlanOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=PlanIn,
        output_model=PlanOut,
        system_template="plan.system.jinja",
        user_template="plan.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="deep",
        reasoning_effort="medium",
        max_output_tokens=8000,
        mock_handler="mde_research_agent.calls.plan:handle",
        untrusted_inputs=("text",),
        cacheable=True,
        repair=True,
        fallback="research.plan",
    ),
    timeout_s=90.0,
    fallback=deterministic_plan,
    validators=(validate,),
)
