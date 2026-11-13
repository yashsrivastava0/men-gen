"""Typed models for the data files in ``mde_research_agent/data``.

Everything the agent knows about WHAT to research (jobs, slot schemas, target templates), HOW MUCH
(load classes, ceilings), WHOM to trust (authority registry, source classes) and HOW TO SAY IT (labels)
lives in data files and loads into these frozen models. Code never hard-codes a domain, a company or a
place; a new country, language or job is a data change.
"""

from __future__ import annotations

from datetime import date
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
# Topics that must not be traded away for a smaller run (D-163): they are kept outside the topic cap.
RiskClass = Literal["regulation", "integration", "identity", "data_protection", "payments"]


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
    # Topics of a risk class (regulation, integrations, identity, data protection, payments) are kept OUTSIDE the target
    # cap: the number the Brief asks for runs. This is only a safety valve for a Brief that asks for far too many.
    risk_topics: int = 99
    # How many discovered providers get their own-site follow-up (documentation, pricing, help) after round one.
    provider_followups: int = 3


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
    # ``country_each`` is ``country`` scope, expanded ONCE PER COUNTRY the Brief names (regulation, provider availability);
    # norms, design and screens stay shared (plan/build.py ``_expand``).
    scope: Literal["city", "country", "country_each", "none"] | None = None
    # ``per_country: true`` does the same for a template whose place breadth is not ``country`` (it keeps its own ``scope``
    # for a single-country Brief and takes the country's name for each named country).
    per_country_flag: bool = Field(default=False, alias="per_country")
    # Which risk class the topic belongs to, if any (see ``RiskClass``): a risk topic is not evicted by the cap.
    risk: RiskClass | None = None
    # With ``risk_from_candidate`` the template's ``risk`` applies only to the candidates that carry evidence of it (a rule
    # topic backed by a compliance fact or by a Brief request about rules); the others are ordinary topics under the cap.
    risk_from_candidate: bool = False
    # Ask for the official pages of the topic's country first: a query variant restricted to the authority domains the
    # registry lists for the topic (provider documentation, rules, platform rules). It replaces one descriptive variant.
    official_first: bool = False
    # ``both``: the topic is also searched on the OTHER configured provider and the two lists are fused (the worker).
    engines: Literal["one", "both"] = "one"
    slots: tuple[SlotSpec, ...]

    @property
    def place_scope(self) -> Literal["city", "country", "none"]:
        if self.scope == "country_each":
            return "country"
        return self.scope or ("none" if self.place == "global" else "city")

    @property
    def per_country(self) -> bool:
        return self.scope == "country_each" or self.per_country_flag

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
    """One official source of the registry (``data/authority/<country>.yaml``).

    ``fetch`` records what happened when a person checked the domain with a plain page request: ``ok`` (it answers and
    names itself), ``blocked`` (a real official site that refuses our reader: its text must come from the search
    provider, never from our fetcher) or ``unresolved`` (it did not answer as written; the entry is kept only when the
    domain was confirmed another way). ``verified_on`` is the date it was checked. ``language`` is the language the
    authority writes in (from the country file), used for rules queries."""

    domain: str
    topics: tuple[str, ...] = ()
    class_: Literal["official", "help", "press"] = Field(default="official", alias="class")
    country: str = "global"
    fetch: Literal["ok", "blocked", "unresolved"] = "ok"
    verified_on: date | None = None
    language: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class SourceClassRules(Frozen):
    official_suffix_patterns: tuple[str, ...]
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


class TopicCost(Frozen):
    searches: float
    usd_micros: int


class PlanConfig(Frozen):
    generic_entities: tuple[str, ...]
    generic_modules: tuple[str, ...]
    generic_topics: tuple[str, ...]
    rule_fact_kinds: tuple[str, ...]
    priority_bonus: dict[str, int]
    platforms: tuple[PlatformSource, ...]
    request_jobs: dict[str, tuple[str, ...]]
    # "job:template" ids of topics about the app as a whole: a request of that job that shares no word with a more
    # specific topic is carried by one of these (plan/routing.py); other jobs leave such a request unmapped.
    request_catch_all: tuple[str, ...] = ()
    max_extra_targets: int
    max_discovery_names: int
    base_terms_max: int
    # A provider topic whose need is listed here is that risk class instead of the template's own (a payments provider
    # is a payments topic, an identity provider an identity topic).
    risk_by_need: dict[str, RiskClass] = Field(default_factory=dict)
    # Needs whose provider topics are searched on both configured engines (``engines: both`` on the template).
    vendor_needs: tuple[str, ...] = ()
    # "job:template" ids of topics about ONE named thing from the Brief (an app the person named): when the thing has no
    # public footprint it ends ``not_found`` after two formulations and no longer counts against the required slots.
    named_reference_templates: tuple[str, ...] = ()
    # How many of a per-country template's best candidates are expanded once per named country (``scope: country_each``);
    # the other candidates stay one shared topic. Bounds the plan of a Brief that names several countries.
    per_country_max: int = 3
    # What one more topic costs (measured on the recorded runs): shown beside each topic that is not run.
    topic_cost: TopicCost
    # The queries of a provider's own-site follow-up: words after its name, restricted to its domain (data, per language
    # of the intent words; the anatomy gate needs an intent word in each).
    entity_followup_words: tuple[str, ...] = ()


class Labels(Frozen):
    depth_words: dict[str, str]
    events: dict[str, str]
    warnings: dict[str, str]
    reasons: dict[str, str]
    declined: str
    declined_user: str


class FigureDrop(Frozen):
    """Kinds whose figure-bearing fields may be nulled (and later claim sentences cut) instead of rejecting the item."""

    fields: dict[str, tuple[str, ...]]


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
    figure_drop: FigureDrop
    languages: dict[str, LanguageInfo]
    labels: dict[str, Labels]
    catalog_hash: str
