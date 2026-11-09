"""Research requests: what the Research agent should look up (prompt 5.4, 6.7).

A request without a geography, an entity or terms is invalid: the model validator rejects it, which is
also how ``cmp.seed_research`` output is checked before anything is stored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mde_compass.domain.common import Strict

Depth = Literal["quick", "standard", "deep"]
Priority = Literal["high", "medium", "low"]


class ResearchRequest(Strict):
    id: str  # RR-###
    topic: str
    question: str
    decision_unlocked: str
    consumer: str = "research"
    entity: str  # the thing researched: a category or a named competitor / service
    geography: str
    source_languages: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)  # user vocabulary plus synonyms
    references: list[str] = Field(default_factory=list)
    prefer_sources: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)  # from the user's exclusions
    depth: Depth = "standard"
    priority: Priority = "medium"
    shown_to_user_as: str = ""
    user_added: bool = False  # added by the user as a chip

    @model_validator(mode="after")
    def _specific(self) -> ResearchRequest:
        missing = [
            name
            for name, value in (
                ("geography", self.geography.strip()),
                ("entity", self.entity.strip()),
                ("terms", [t for t in self.terms if t.strip()]),
                ("topic", self.topic.strip()),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"research request is not specific: missing {', '.join(missing)}")
        return self


class SearchHints(Strict):
    disambiguated_names: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    local_competitors: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
