"""Typed models for the data files in ``mde_research_agent/data``.

Everything the agent knows about WHAT to research (jobs, slot schemas, target templates), HOW MUCH
(load classes, ceilings), WHOM to trust (authority registry, source classes) and HOW TO SAY IT (labels)
lives in data files and loads into these frozen models. Code never hard-codes a domain, a company or a
place; a new country, language or job is a data change.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

JobName = Literal[
    "features", "structure", "flows", "data", "services", "design", "screens", "local"
]
LaneName = Literal["blueprint", "integrations", "design", "screens", "mock", "verify"]
LoadClass = Literal["focused", "standard", "deep"]
Need = Literal["current", "norm", "entity", "number"]
Foreach = Literal[
    "none",
    "reference",
    "role",
    "flow",
    "entity",
    "module",
    "need",
    "rule_topic",
    "format_topic",
    "platform",
    "script_locale",
]
Condition = Literal["non_latin_locale", "has_integrations", "multi_app", "has_sensitive_data"]
Anchor = Literal[
    "category", "entity", "role", "flow", "module", "need", "topic", "platform", "language"
]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- load and ceilings -----------------------------------------------------------------------


class LoadWeights(Frozen):
    feature: int
    fact: int
    app: int
    sensitive: int
    reference: int


class LoadCut(Frozen):
    name: LoadClass
    below: int | None = None


class LoadConfig(Frozen):
    weights: LoadWeights
    fact_kinds: tuple[str, ...]
    classes: tuple[LoadCut, ...]


class Ceiling(Frozen):
    searches: int
    pages: int
    seconds: int
    rounds: int
    lanes_search: int
    lanes_extract: int
    research_tokens: int
    slice_tokens: int
    targets: int
    followups_per_topic: int


class CeilingSet(Frozen):
    soft: Ceiling
    hard: Ceiling


class PagesPerTopic(Frozen):
    min: int
    max: int


class CeilingsConfig(Frozen):
    focused: CeilingSet
    standard: CeilingSet
    deep: CeilingSet
    min_round_gain: float
    pages_per_topic: PagesPerTopic
    max_per_domain: int
    triage_model_above: int
    variants_per_topic: int

    def for_class(self, name: LoadClass) -> CeilingSet:
        return getattr(self, name)


# --- jobs and slots ----------------------------------------------------------------------------


class ExtraAttr(Frozen):
    attr: str
    min: int


class SlotSpec(Frozen):
    """What must be found for a topic to count as covered.

    ``count``: at least ``min`` items of ``kind`` (distinct by ``distinct_by``); when ``attr`` is set an
    item only counts when that list attribute has at least ``attr_min`` entries (and every ``extra_attrs``
    rule holds). ``distinct_values``: at least ``min`` distinct values across the ``attr`` of all items of
    ``kind``.
    """

    key: str
    type: Literal["count", "distinct_values"]
    kind: str
    min: int = 1
    distinct_by: str = "norm_key"
    attr: str | None = None
    attr_min: int = 0
    extra_attrs: tuple[ExtraAttr, ...] = ()
    required: bool = True


class TargetTemplate(Frozen):
    id: str
    label: str
    foreach: Foreach = "none"
    max: int = 1
    when: tuple[Condition, ...] = ()
    question: str
    decision_unlocked: str
    needs: tuple[Need, ...]
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    anchor: Anchor = "category"
    place: Literal["brief", "global"] = "brief"
    slots: tuple[SlotSpec, ...]


class JobSpec(Frozen):
    job: JobName
    spec_need: str
    lane: LaneName
    also_lanes: tuple[LaneName, ...] = ()
    recency: bool = False
    intent: tuple[str, ...]
    kinds: tuple[str, ...]
    extract_focus: str
    targets: tuple[TargetTemplate, ...]


# --- sources and authority -------------------------------------------------------------------------


class AuthorityEntry(Frozen):
    domain: str
    topics: tuple[str, ...] = ()
    class_: Literal["official", "help", "press"] = Field(default="official", alias="class")
    country: str = "global"

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class SourceClassRules(Frozen):
    official_tld_suffixes: tuple[str, ...]
    doc_subdomain_prefixes: tuple[str, ...]
    help_subdomain_prefixes: tuple[str, ...]
    store_domains: tuple[str, ...]
    forum_domains: tuple[str, ...]
    forum_subdomain_prefixes: tuple[str, ...]
    blog_domains: tuple[str, ...]
    blog_path_markers: tuple[str, ...]
    seo_domains: tuple[str, ...]
    seo_title_patterns: tuple[str, ...]
    press_domains: tuple[str, ...]
    cannot_support_high_impact: tuple[str, ...]
    authoritative: tuple[str, ...]


class IntentLanguage(Frozen):
    reviewed: bool
    words: tuple[str, ...]


class IntentWords(Frozen):
    languages: dict[str, IntentLanguage]
    generic_words: tuple[str, ...]


class TemplatePatterns(Frozen):
    repeated_word_regex: str
    category_suffix_words: tuple[str, ...]
    repeated_audience_share: float
    template_fact_prefixes: tuple[str, ...]
    junk_tokens: tuple[str, ...]


class StaleMonths(Frozen):
    default: int
    recency_jobs: int
    fees: int


class PrevalenceConfig(Frozen):
    min_sources: int
    core_share: float
    common_share: float
    emerging_max_age_months: int
    emerging_max_seen: int
    stale_months: StaleMonths
    recency_jobs: tuple[str, ...]
    fee_words: tuple[str, ...]


class KindLane(Frozen):
    lanes: tuple[LaneName, ...]
    need: str
    area: str


class LanesConfig(Frozen):
    lanes: tuple[LaneName, ...]
    kinds: dict[str, KindLane]
    high_impact_kinds: tuple[str, ...]
    legal_adjacent_kinds: tuple[str, ...]
    norm_areas: tuple[str, ...]


class NeedKeywords(Frozen):
    label: str
    words: tuple[str, ...]


class NeedsConfig(Frozen):
    needs: dict[str, NeedKeywords]
    format_words: tuple[str, ...]


class Labels(Frozen):
    depth_words: dict[str, str]
    events: dict[str, str]
    warnings: dict[str, str]
    reasons: dict[str, str]
    declined: str


class Catalog(Frozen):
    """Everything loaded from the data folder, validated, with a content hash."""

    load: LoadConfig
    ceilings: CeilingsConfig
    jobs: dict[str, JobSpec]
    authority: tuple[AuthorityEntry, ...]
    source_rules: SourceClassRules
    intent_words: IntentWords
    template_patterns: TemplatePatterns
    prevalence: PrevalenceConfig
    lanes: LanesConfig
    needs: NeedsConfig
    labels: dict[str, Labels]
    catalog_hash: str
