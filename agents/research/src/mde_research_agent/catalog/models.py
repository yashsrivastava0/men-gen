"""Typed models for the data files in ``mde_research_agent/data``.

Everything the agent knows about WHAT to research (jobs, slot schemas, target templates), HOW MUCH
(load classes, ceilings), WHOM to trust (authority registry, source classes) and HOW TO SAY IT (labels)
lives in data files and loads into these frozen models. Code never hard-codes a domain, a company or a
place; a new country, language or job is a data change.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobName = Literal[
    "features", "structure", "flows", "data", "services", "design", "screens", "local", "trust"
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
Condition = Literal[
    "non_latin_locale",
    "has_integrations",
    "multi_app",
    "has_sensitive_data",
    "trust",
    "payments",
    "messaging",
    "maps_location",
    "tracking",
    "identity_kyc",
    "government_documents",
    "accounting",
    "scheduling",
    "two_sided",
    "media_upload",
    "health_data",
    "minors",
    "multi_region",
    "many_roles",
    "sensitive_data",
]
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
    class_margin: int = (
        0  # a score within this many points BELOW a boundary already uses the larger class
    )


class SignalWords(Frozen):
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()


class LightConfig(Frozen):
    below: int
    templates: tuple[str, ...]
    soft_searches: int
    soft_seconds: int


class TrustConfig(Frozen):
    always_for: tuple[str, ...]
    triggers: tuple[str, ...]
    together: tuple[str, ...]


class StructureSignals(Frozen):
    many_roles_at_least: int
    multi_app_at_least: int


class SignalsConfig(Frozen):
    signals: dict[str, SignalWords]
    structure: StructureSignals
    trust: TrustConfig
    light: LightConfig
    need_signal: dict[str, str]


class Ceiling(Frozen):
    """What a run aims at (soft) for one size class."""

    searches: int
    pages: int
    seconds: int
    rounds: int
    lanes_search: int
    lanes_extract: int
    targets: int
    followups_per_topic: int


class HardLimits(Frozen):
    """What a run never exceeds (RV-12)."""

    searches: int
    pages: int
    seconds: int
    rounds: int
    followups_per_topic: int


class CeilingSet(Frozen):
    soft: Ceiling
    hard: HardLimits
    # The three caps (AD-55): nothing is deleted to fit; items above the store limit move to the appendix.
    store_tokens: int  # the main store, measured on the compact view
    chunk_items: int  # a lane slice is split into chunks of at most this many items
    digest_tokens: int  # each lane digest (an index, outside store_tokens)


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
    ``kind`` (each value supported by at least ``min_support`` distinct items).
    """

    key: str
    type: Literal["count", "distinct_values"]
    kind: str
    min: int = 1
    distinct_by: str = "norm_key"
    attr: str | None = None
    attr_min: int = 0
    extra_attrs: tuple[ExtraAttr, ...] = ()
    deep_min: int | None = None  # a larger minimum for the deep class
    min_support: int = (
        1  # distinct_values: a value counts only when this many distinct items show it
    )
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
    # How much of the Brief's place the topic's queries carry: the city and country (``city``, the default), the country
    # only, or none (a technical doc, a flow, a screen pattern or a global vendor is not found by a city name).
    scope: Literal["city", "country", "none"] | None = None
    slots: tuple[SlotSpec, ...]

    @property
    def place_scope(self) -> Literal["city", "country", "none"]:
        return self.scope or ("none" if self.place == "global" else "city")

    @model_validator(mode="after")
    def _scope_agrees_with_place(self) -> TargetTemplate:
        if self.place == "global" and self.scope not in (None, "none"):
            raise ValueError(f"template {self.id}: place: global cannot have scope {self.scope}")
        return self


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
    walled_domains: tuple[
        str, ...
    ] = ()  # refuse our reader: read only when the search provider returned the text
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
    own_brand_cues: tuple[str, ...]
    fragment_term_chars: int


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
    terms: tuple[str, ...] = ()
    min_all: tuple[str, ...] = ()
    min_any: tuple[str, ...] = ()


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


class LanguageInfo(Frozen):
    name: str
    script: str
    direction: Literal["ltr", "rtl"]
    latin: bool


class LanguagesConfig(Frozen):
    languages: dict[str, LanguageInfo]


class PlatformSource(Frozen):
    name: str
    domain: str


class PlanConfig(Frozen):
    generic_entities: tuple[str, ...]
    generic_modules: tuple[str, ...]
    generic_topics: tuple[str, ...]
    rule_fact_kinds: tuple[str, ...]
    priority_bonus: dict[str, int]
    platforms: tuple[PlatformSource, ...]
    request_jobs: dict[str, tuple[str, ...]]
    max_extra_targets: int
    max_discovery_names: int
    base_terms_max: int


class Labels(Frozen):
    depth_words: dict[str, str]
    events: dict[str, str]
    warnings: dict[str, str]
    reasons: dict[str, str]
    declined: str
    declined_user: str


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
    plan: PlanConfig
    signals: SignalsConfig
    languages: dict[str, LanguageInfo]
    labels: dict[str, Labels]
    catalog_hash: str
