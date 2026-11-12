"""``research.v5``: the playbook Spec reads (doc 13 section 9).

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

from mde_research_agent.domain.atoms import EffectOnBuild, Platform, TypedValue

SCHEMA = "research.v5"
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
    "partial_support",
    "other_region",
    "other_entity",
    "duplicate_source",
    "number_widened",
    "figures_dropped",
    "tightened",
]
Evidence = Literal["official", "corroborated", "single_source", "unverified"]
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
    evidence: Evidence = "unverified"
    source_language: str | None = None
    about_region: str = "unknown"
    prevalence: Prevalence | None = None
    recency: Recency | None = None
    request_ids: list[str] = Field(default_factory=list)
    target_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    # Figure-bearing text fields on which another merged source states a DIFFERENT figure than ``data``: field -> the other
    # sources' text. Both are kept; the item is flagged ``conflict`` (D-159: a conflicting scalar never collapses silently).
    variants: dict[str, list[str]] = Field(default_factory=dict)
    # The main store keeps what Spec reads; everything else is the appendix: nothing is deleted to fit (AD-55).
    store: Literal["main", "appendix"] = "main"
    appendix_reason: str | None = None
    tokens_est: int = 0  # characters over 4 of the item's COMPACT view
    rank: int = 0  # 1 is best (doc 13 7.2); the same rule decides what is protected


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


ConfirmClass = Literal["money", "legal", "identity", "other"]


class Confirm(Model):
    item_id: str
    why: str
    # Added by code (P6): the order the owner should read the list in. Display is the front end's job.
    rank: int = (
        0  # 1 is first: money, then legal, then identity, then the rest, by impact inside a class
    )
    group: str = ""  # the topic the item belongs to (its job and kind, or the provider or capability it is about)
    class_: ConfirmClass = Field(default="other", alias="class")
    top: bool = False  # one of the 20 first

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- provider dossiers, platform constraints, the compliance register and the lane decisions (D-159, D-162) -----------
# All four are built by CODE from the checked items (assemble/dossier.py, platform.py, register.py, decisions.py): the model
# never writes them. Each value points at the items that state it, with the evidence label of those items.

NullReason = Literal["not_stated_by_sources", "not_researched", "conflict"]


class DossierValue(Model):
    value: str
    typed: TypedValue | None = None
    item_id: str
    evidence: Evidence
    newest_source: date | None = None
    domain: str | None = None  # the domain of the item's best source
    source_ids: list[str] = Field(default_factory=list)
    conflict: bool = False  # another usable value of the same field, unit, period, condition, currency and country differs


class DossierField(Model):
    """One field of a provider dossier. ``value`` is the single value (a scalar, one typed figure) or the usable values
    joined with `` | `` (a list field); it is null, with a ``null_reason``, when no item states the field or when usable
    values conflict. ``evidence`` is the WEAKEST label among the values in ``value``. ``values`` keeps every value."""

    value: str | None = None
    typed: TypedValue | None = None  # the typed value when the field has exactly one value
    values: list[DossierValue] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    evidence: Evidence | None = None
    null_reason: NullReason | None = None


class Completeness(Model):
    filled: int  # required fields with a usable (not unverified, not conflicting) value
    required: int
    share: float
    unverified: int = 0  # required fields whose only values are unverified


class Dossier(Model):
    id: str
    provider: str
    capability: str | None = (
        None  # the integration need (a key of need_keywords.yaml) the provider serves
    )
    needs: list[str] = Field(default_factory=list)  # as the pages state them
    official_url: str | None = (
        None  # the provider's own documentation page, set by code from the sources
    )
    fields: dict[str, DossierField] = Field(
        default_factory=dict
    )  # the layout's fields, then any other field with values
    required: list[str] = Field(default_factory=list)  # field keys that count into completeness
    completeness: Completeness
    official_share: float = 0.0  # filled fields whose evidence is official over all filled fields
    item_ids: list[str] = Field(default_factory=list)  # every provider fact of the dossier
    service_option_ids: list[str] = Field(
        default_factory=list
    )  # service_option items of the same provider
    integrations: list[str] = Field(default_factory=list)  # INT keys of its items' applies_to


class PlatformConstraint(Model):
    item_id: str
    capability: str  # a capability id of capabilities.yaml when the stated text matches one, else the stated text
    platform: Platform | None = None
    rule: str
    effect_on_build: EffectOnBuild | None = None
    status: str | None = None
    evidence: Evidence
    domain: str | None = None


class RegisterEntry(Model):
    item_id: str
    topic: str
    statement: str
    authority: str | None = None
    effective_date: str | None = None
    applies_to: list[str] = Field(default_factory=list)
    penalty: str | None = None
    domain: str | None = None
    evidence: Evidence


class RegisterRegime(Model):
    regime: str
    entries: list[RegisterEntry]


class RegisterCountry(Model):
    country: str  # lowercase ISO code, ``global`` or ``unknown``
    regimes: list[RegisterRegime]


DecisionGapReason = Literal["not_planned", "not_found", "not_run", "unverified_only"]


class DecisionGap(Model):
    reason: DecisionGapReason
    note: str = ""


class LaneDecisionOut(Model):
    id: str
    lane: Lane
    text: str
    item_ids: list[str] = Field(
        default_factory=list
    )  # main-store items of the lane with a usable (not unverified) label
    unverified_item_ids: list[str] = Field(default_factory=list)
    best_evidence: Evidence | None = None
    gap: DecisionGap | None = (
        None  # set exactly when ``item_ids`` is empty: Spec assumes this decision
    )


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
    """Every main-store item of the lane (ordered by code, the model's order first), split into chunks of at most
    ``chunk_items``; the digest is an index and sits outside ``store_tokens``."""

    item_ids: list[str] = Field(default_factory=list)
    chunks: list[list[str]] = Field(default_factory=list)
    digest: str = ""


class Profile(Model):
    load_score: int
    load_class: Literal["focused", "standard", "deep"]
    rounds: int = 0
    light: bool = False
    signals: list[str] = Field(default_factory=list)
    store_tokens: int = 0  # main store, measured on the compact view
    appendix_items: int = 0
    digest_tokens: int = 0  # kept for compatibility: the same number as digest_tokens_total
    digest_tokens_total: int = 0  # the six lane digests together, as measured
    digest_tokens_cap_per_lane: int = (
        0  # the cap of ONE lane digest; the six lanes can together use up to six times it
    )
    store_over_limit: bool = (
        False  # the protected items alone were over store_tokens: nothing was moved
    )


LaneQualityValue = Literal["strong", "partial", "thin", "none"]
BnValue = Literal["researched", "single_source_only", "defaulted_only", "gap"]


class LaneQuality(Model):
    quality: LaneQualityValue
    slots_required: int = 0
    slots_filled: int = 0
    items: int = 0
    strong_share: float = 0.0  # official or corroborated items over the lane's items


class Validation(Model):
    errors: int = 0
    warnings: int = 0
    by_rule: dict[str, int] = Field(default_factory=dict)


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
    """Why extracted items did not become items. Every drop has a counted reason; nothing is lost silently (R26)."""

    quote_not_found: int = 0
    number_not_in_quote: int = 0
    not_supported: int = 0
    unsupported_field: int = 0
    forbidden_pattern: int = 0
    claim_too_long: int = 0
    invalid: int = 0


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
    stages: dict[str, float] = Field(
        default_factory=dict
    )  # work seconds by stage (parallel work adds up)
    cost_by_step: dict[str, int] = Field(
        default_factory=dict
    )  # micro-USD by step (search, plan, queries, extract, check, assemble)
    fallbacks: dict[str, int] = Field(default_factory=dict)  # degraded paths used, by call id


class ResearchOutput(Model):
    schema_: str = Field(default=SCHEMA, alias="schema")
    program_id: str
    brief_hash: str
    status: Status
    profile: Profile
    input_quality: dict[str, Any] = Field(
        description=(
            "How far the Brief could be trusted. Keys include `input_binding` (verified | unverified_no_baseline | "
            "unverified_hash_mismatch: whether the export's own approval seal could be checked), `compass_format` "
            "(the export's format version, or null), `plan_source`, `requests`, `failures` and `flags`. An unverified "
            "Brief is researched, never refused; this says so."
        )
    )
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
    dossiers: list[Dossier] = Field(default_factory=list)
    dossiers_by_integration: dict[str, list[str]] = Field(default_factory=dict)
    platform_constraints: list[PlatformConstraint] = Field(default_factory=list)
    compliance_register: list[RegisterCountry] = Field(default_factory=list)
    lane_decisions: list[LaneDecisionOut] = Field(default_factory=list)
    slices: dict[str, Slice] = Field(default_factory=dict)
    claims_used: list[str] = Field(default_factory=list)
    dropped: Dropped = Field(default_factory=Dropped)
    flagged: dict[str, int] = Field(default_factory=dict)
    lane_quality: dict[str, LaneQuality] = Field(default_factory=dict)
    bn_coverage: dict[str, BnValue] = Field(default_factory=dict)
    validation: Validation = Field(default_factory=Validation)
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
