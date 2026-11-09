"""Situation Profile, Discovery Plan and Domain Hypothesis (prompt 6.1, 6.2 step 4).

Everything in a ``DomainHypothesis`` is a hypothesis, never a fact; the playback card lets the user
correct it before anything is written to the Brief.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_compass.domain.common import Strict

InputRichness = Literal["nothing", "one_sentence", "rich", "documents"]
Clarity = Literal["vague", "knows_features", "knows_everything"]
Expertise = Literal["non_technical", "product", "technical"]
SizeStakes = Literal["tiny", "startup", "enterprise"]
Collaboration = Literal["one", "several"]
Urgency = Literal["just_build", "thorough"]
Register = Literal["casual", "professional"]
DepthProfile = Literal["express", "standard", "large"]


class SituationProfile(Strict):
    input_richness: InputRichness = "nothing"
    clarity: Clarity = "vague"
    expertise: Expertise = "non_technical"
    size_stakes: SizeStakes = "startup"
    reference_driven: bool = False
    collaboration: Collaboration = "one"
    language: str = "en"
    input_mode: Literal["text", "voice"] = "text"
    urgency: Urgency = "thorough"
    formality: Register = "casual"


class DiscoveryPlan(Strict):
    depth: DepthProfile = "standard"
    modules: list[str] = Field(
        default_factory=lambda: [
            "understand",
            "playback",
            "gaps",
            "propose",
            "seed_research",
            "probes",
        ]
    )
    question_budget: int = 7
    minimum_viable: bool = False


class HypothesisFeature(Strict):
    family: str
    name: str
    intent: str
    custom: bool = False  # outside families.yaml: flagged and confirmed by the user


class GlossaryCandidate(Strict):
    term: str
    synonyms: list[str] = Field(default_factory=list)


class DomainHypothesis(Strict):
    label: Literal["hypothesis"] = "hypothesis"
    category: str = ""
    subcategory: str = ""
    business_model_candidates: list[str] = Field(default_factory=list)
    likely_families: list[str] = Field(default_factory=list)
    likely_roles: list[str] = Field(default_factory=list)
    likely_features: list[HypothesisFeature] = Field(default_factory=list)
    hard_parts: list[str] = Field(default_factory=list)
    glossary_candidates: list[GlossaryCandidate] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
