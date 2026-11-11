"""``research.v4``: the playbook Spec reads (doc 13 section 9).

Short strings, enums and no markdown inside fields: easy for a model to read and for a front end to
render. Every item carries its sources, prevalence and recency; ``tier`` is ``default`` or ``open`` and
NEVER ``locked`` (Research enriches and never decides). Quotes are not here: they stay in the internal
evidence (``research_evidence``), which nothing downstream reads. Volatile values (run id, timestamps,
seconds, model ids) live in the top-level ``run`` block, which is excluded from the content hash, so
the same inputs and the same cassettes give the same hash.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "research.v4"
Status = Literal["complete", "partial", "failed", "skipped"]
Level = Literal["core", "common", "advanced", "emerging", "limited_evidence"]
SourceClass = Literal["official", "store", "help", "press", "blog", "forum", "seo"]
Impact = Literal["normal", "high"]
Confidence = Literal["high", "medium", "low"]
Trend = Literal["stable", "rising", "declining", "unknown"]
Flag = Literal[
    "single-source",
    "conflict",
    "stale",
    "needs-confirm",
    "legal-adjacent",
    "entity_from_model",
    "assumption_derived",
    "geo_uncertain",
    "limited_evidence",
    "withdrawn",
    "unchecked",
    "checker_same_vendor",
]
AppliesKind = Literal["FTR", "ROL", "APP", "MOD", "ENT", "FLW", "INT", "NEW"]
Relation = Literal["expects", "constrains", "refines", "alternative", "suggests"]
GapReason = Literal[
    "not_found",
    "budget_stop",
    "provider_down",
    "not_run",
    "not_worthwhile",
    "conflict_unresolved",
    "user_cancelled",
    "ambiguous_reference",
    "declined",
]
Lane = Literal["blueprint", "integrations", "design", "screens", "mock", "verify"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(Model):
    id: str
    domain: str
    source_class: SourceClass
    url: str
    retrieved_on: date
    published_on: date | None = None


class Prevalence(Model):
    seen_in: int
    of: int
    level: Level


class Recency(Model):
    newest_source: date | None = None
    age_months: int | None = None
    trend: Trend = "unknown"


class AppliesTo(Model):
    kind: AppliesKind
    id: str | None = None
    relation: Relation = "expects"


class Item(Model):
    id: str
    job: str
    spec_need: str
    kind: str
    label: str
    claim: str
    use_note: str = ""
    slot: str | None = None
    impact: Impact = "normal"
    confidence: Confidence = "medium"
    tier: Literal["default", "open"] = "default"
    flags: list[Flag] = Field(default_factory=list)
    applies_to: list[AppliesTo] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    independent_domains: int = 0
    prevalence: Prevalence | None = None
    recency: Recency | None = None
    request_ids: list[str] = Field(default_factory=list)
    target_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Norm(Model):
    area: Literal["feature", "screen", "flow", "entity", "integration", "design"]
    key: str
    label: str
    prevalence: Prevalence
    recency: Recency
    item_ids: list[str]
    matches_brief: str | None = None


class Suggestion(Model):
    label: str
    item_ids: list[str]
    prevalence: Prevalence | None = None
    state: Literal["undecided"] = "undecided"


class Suggestions(Model):
    features: list[Suggestion] = Field(default_factory=list)
    screens: list[Suggestion] = Field(default_factory=list)
    fields: list[Suggestion] = Field(default_factory=list)
    states: list[Suggestion] = Field(default_factory=list)
    validations: list[Suggestion] = Field(default_factory=list)


class DefaultProposed(Model):
    need: str
    item_ids: list[str]
    default: str
    state: Literal["defaulted"] = "defaulted"


class Confirm(Model):
    item_id: str
    why: str


class Gap(Model):
    target_id: str
    reason: GapReason
    label: str = ""
    queries: list[str] = Field(default_factory=list)


class Conflict(Model):
    topic: str
    item_ids: list[str]
    note: str = ""


class Slice(Model):
    item_ids: list[str] = Field(default_factory=list)
    digest: str = ""


class Profile(Model):
    load_score: int
    load_class: Literal["focused", "standard", "deep"]
    rounds: int = 0


class Budget(Model):
    searches_used: int = 0
    pages_read: int = 0
    seconds: int = 0
    usd: str = "0"
    usd_micros: int = 0
    failures: int = 0


class Coverage(Model):
    targets: int = 0
    covered: int = 0
    not_found: int = 0
    slots_required: int = 0
    slots_filled: int = 0


class Dropped(Model):
    quote_not_found: int = 0
    not_supported: int = 0
    size_cap: int = 0
    forbidden_pattern: int = 0
    unchecked_high_impact: int = 0


class RunInfo(Model):
    """Volatile values: excluded from the content hash."""

    run_id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    as_of: date
    catalog_hash: str = ""
    writer_model: str = ""
    checker_model: str = ""
    checker_vendor: Literal["same", "different", "none"] = "none"
    search_provider: str = ""
    plan_source: str = ""
    notes: list[str] = Field(default_factory=list)


class ResearchOutput(Model):
    schema_: str = Field(default=SCHEMA, alias="schema")
    program_id: str
    brief_hash: str
    status: Status
    profile: Profile
    input_quality: dict[str, Any]
    budget: Budget = Field(default_factory=Budget)
    coverage: Coverage = Field(default_factory=Coverage)
    by_feature: dict[str, list[str]] = Field(default_factory=dict)
    by_role: dict[str, list[str]] = Field(default_factory=dict)
    by_entity: dict[str, list[str]] = Field(default_factory=dict)
    by_flow: dict[str, list[str]] = Field(default_factory=dict)
    by_integration: dict[str, list[str]] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list)
    norms: list[Norm] = Field(default_factory=list)
    suggestions: Suggestions = Field(default_factory=Suggestions)
    defaults_proposed: list[DefaultProposed] = Field(default_factory=list)
    confirm: list[Confirm] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    slices: dict[str, Slice] = Field(default_factory=dict)
    claims_used: list[str] = Field(default_factory=list)
    dropped: Dropped = Field(default_factory=Dropped)
    run: RunInfo

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def content_payload(output: dict[str, Any]) -> dict[str, Any]:
    """The part of a dumped output that the content hash covers: everything except ``run`` and the
    volatile budget values (seconds and money depend on the wall clock and the provider's prices)."""
    body = {k: v for k, v in output.items() if k != "run"}
    budget = dict(body.get("budget", {}))
    for volatile in ("seconds", "usd", "usd_micros"):
        budget.pop(volatile, None)
    body["budget"] = budget
    return body
