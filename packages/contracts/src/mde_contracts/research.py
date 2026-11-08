"""Research Note (spec 4): facts, suggestions, flags, each with a source and a retrieval date."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from mde_contracts.common import Depth, Frozen, ResearchRefId
from mde_core.hashing import hash_model

ResearchJobId = Literal["R1", "R2", "R3", "R4", "R5", "R6"]
Confidence = Literal["high", "medium", "low"]
Impact = Literal["low", "high"]
FactKind = Literal["feature", "pattern", "brand", "compliance", "integration", "other"]
SuggestionKind = Literal["feature", "pattern", "compliance", "integration", "brand"]
ResearchStatus = Literal["ok", "partial", "unavailable"]


class SourceRef(Frozen):
    url: str
    title: str | None = None
    domain: str | None = None
    retrieved_at: date


class ResearchFact(Frozen):
    id: str  # "F-001"
    job: ResearchJobId
    kind: FactKind = "other"
    statement: str  # short paraphrase, never a long quote
    sources: list[SourceRef]  # >= 1; high-impact facts need two independent domains
    impact: Impact = "low"
    confidence: Confidence = "medium"
    verified: bool = False  # False => shown as "unverified"


class ResearchSuggestion(Frozen):
    """A research-driven addition. Appears at Gate 2 as 'What research added' with a toggle."""

    id: ResearchRefId  # "RA-001"; spec items carry this in ``research_ref``
    kind: SuggestionKind
    title: str
    rationale: str
    fact_ids: list[str] = []
    default_included: bool = True
    applies_to: str | None = None  # slot id or spec section it would change
    verified: bool = False


class BrandFinding(Frozen):
    source_url: str
    colors: list[str] = []
    fonts: list[str] = []
    tone_words: list[str] = []
    logo_available: bool = False
    nearest_direction: str | None = None  # theme preset id


class CompliancePointer(Frozen):
    flag: str  # compliance flag id from the Domain Pack
    title: str
    url: str
    note: str = "Pointer only; not legal advice."


class IntegrationNote(Frozen):
    provider: str
    sdk_available: bool | None = None
    expo_compatible: bool | None = None
    caveats: list[str] = []
    source: SourceRef | None = None


class ResearchNote(Frozen):
    version: int = 1
    hash: str = ""
    brief_hash: str  # hash of the approved Brief this was built from
    depth: Depth = "light"
    status: ResearchStatus = "ok"
    unavailable_reason: str | None = None  # policy | budget | failed | mock_no_fixtures | ...
    facts: list[ResearchFact] = []
    suggestions: list[ResearchSuggestion] = []
    compliance: list[CompliancePointer] = []
    brand: BrandFinding | None = None
    integrations: list[IntegrationNote] = []
    gaps: list[str] = []
    queries_run: int = 0
    cost_micros: int = 0
    created_at: datetime | None = None

    def compute_hash(self) -> str:
        return hash_model(self, exclude={"hash", "created_at", "cost_micros"})

    def with_hash(self) -> ResearchNote:
        return self.model_copy(update={"hash": self.compute_hash()})

    @classmethod
    def unavailable(cls, brief_hash: str, reason: str, depth: Depth = "light") -> ResearchNote:
        return cls(
            brief_hash=brief_hash, status="unavailable", unavailable_reason=reason, depth=depth
        ).with_hash()
