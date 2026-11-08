"""Wire models of ``cmp.situate`` (strict-schema-safe: all fields required, depth <= 5, no open objects)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatementWire(Wire):
    type: str
    text: str


class FamilyWire(Wire):
    id: str
    label: str
    keywords: list[str]
    typical_features: list[str]  # at most 6 names


class SituateInput(Wire):
    user_text: str  # untrusted
    statements: list[StatementWire]  # at most 40
    language: str
    mode: Literal["guided", "enterprise"]
    input_mode: Literal["text", "voice"]
    families: list[FamilyWire]


class ProfileWire(Wire):
    input_richness: Literal["nothing", "one_sentence", "rich", "documents"]
    clarity: Literal["vague", "knows_features", "knows_everything"]
    expertise: Literal["non_technical", "product", "technical"]
    size_stakes: Literal["tiny", "startup", "enterprise"]
    reference_driven: bool
    collaboration: Literal["one", "several"]
    language: str
    input_mode: Literal["text", "voice"]
    urgency: Literal["just_build", "thorough"]
    formality: Literal["casual", "professional"]


class FeatureWire(Wire):
    family: str
    name: str
    intent: str
    custom: bool


class TermWire(Wire):
    term: str
    synonyms: list[str]


class HypothesisWire(Wire):
    label: Literal["hypothesis"]
    category: str
    subcategory: str
    business_model_candidates: list[str]
    likely_families: list[str]
    likely_roles: list[str]
    likely_features: list[FeatureWire]
    hard_parts: list[str]
    glossary_candidates: list[TermWire]
    confidence: float


class SituateOutput(Wire):
    profile: ProfileWire
    hypothesis: HypothesisWire
    one_line_idea: str
