"""Wire models of ``cmp.probe_handoff`` (strict-schema-safe: flat, all fields required, no open objects).

One spec serves the three probes; ``kind`` selects which planner the prompt asks for. The output carries
the fields of all three (the unused ones stay empty) so one schema fits.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MAX_ITEMS = 40
MIN_QUERIES = 8
MIN_SCREENS = 3


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DigestApp(Wire):
    name: str
    purpose: str


class DigestRole(Wire):
    name: str
    goal: str


class DigestFeature(Wire):
    name: str
    intent: str
    priority: str  # must | nice | later
    status: str  # in | later | out | unsupported | needs-engineering


class Digest(Wire):
    """The Brief reduced to what a downstream planner would be handed (no chat, no provenance)."""

    idea: str
    category: str
    business_model: str
    geography: str
    audience: list[str]
    apps: list[DigestApp]
    roles: list[DigestRole]
    features: list[DigestFeature]  # at most MAX_ITEMS
    glossary: list[str]
    references: list[str]
    tone_words: list[str]
    brand_signals: list[str]
    locales: list[str]
    constraints: list[str]


class ProbeHandoffInput(Wire):
    kind: Literal["research", "blueprint", "design"]
    language: str
    digest: Digest


class PlanQuery(Wire):
    query: str
    region: str
    entity: str
    terms: list[str]


class Journey(Wire):
    name: str
    role: str
    steps: list[str]


class Screen(Wire):
    name: str
    journey: str
    first_release: bool


class BlockingQuestion(Wire):
    question: str
    slot_id: str | None
    fix: Literal["assume", "ask"]
    suggested_value_json: str | None  # a JSON text, parsed and checked by code


class ProbeHandoffOutput(Wire):
    can_start: bool  # the planner's own claim; code never trusts it
    plan_queries: list[PlanQuery]  # research
    journeys: list[Journey]  # blueprint
    screens: list[Screen]  # blueprint
    tone: str  # design
    direction: str  # design
    blocking_questions: list[BlockingQuestion]
