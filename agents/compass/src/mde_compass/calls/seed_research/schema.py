"""Wire models of ``cmp.seed_research`` (strict-schema-safe: flat, all fields required, no open objects)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_FEATURES = 20
MAX_REQUESTS = 12
MIN_REQUESTS = 3
FALLBACK_MAX_REQUESTS = 8
MAX_DETAILS = 30
DETAIL_CHARS = 160


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeoIn(Wire):
    country: str
    city: str
    region: str


class GlossaryIn(Wire):
    term: str
    synonyms: list[str]


class FeatureIn(Wire):
    name: str
    intent: str


class DetailIn(Wire):
    """A detail the person gave that research can use: a rule, a number, a compliance duty, a named system."""

    kind: str
    text: str


class SeedResearchInput(Wire):
    language: str
    category: str
    subcategory: str
    geography: GeoIn
    locale_country: str  # from the user's locale when known, else ""
    audience: list[str]
    business_model: str
    references: list[str]  # apps the user named ("like X")
    glossary: list[GlossaryIn]
    exclusions: list[str]  # scope.must_not: carried into every request's ``avoid``
    unknowns: list[str]  # slots the Brief could not fill (research wanted)
    features: list[FeatureIn]  # at most MAX_FEATURES
    families: list[str]  # likely capability families
    languages: list[str]  # app locales
    details: list[DetailIn] = Field(
        default_factory=list
    )  # v2: rules, numbers, compliance, integrations (<= 30)


class WireRequest(Wire):
    topic: str
    question: str
    decision_unlocked: str
    entity: str
    geography: str
    source_languages: list[str]
    terms: list[str]
    references: list[str]
    prefer_sources: list[str]
    avoid: list[str]
    depth: Literal["quick", "standard", "deep"]
    priority: Literal["high", "medium", "low"]
    shown_to_user_as: str


class WireHints(Wire):
    disambiguated_names: list[str]
    synonyms: list[str]
    local_competitors: list[str]  # ONLY names the user gave
    languages: list[str]


class SeedResearchOutput(Wire):
    requests: list[WireRequest]
    hints: WireHints
