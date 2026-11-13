"""What Research understood from an approved Compass export (``ResearchInput``) and how far to trust it.

Built by ``intake`` from the format-2 export (plain JSON, never by importing Compass). Values carry their
TIER: ``locked`` is the person's verified words, ``default`` is an AI guess, ``open`` is unresolved
(trust rule 1: approval is not review). The tier of the interview (Quick, Standard, Thorough) is NOT an
input: nothing here records it, and ``load`` is computed from the content alone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Tier = Literal["locked", "default", "open"]
LoadClass = Literal["focused", "standard", "deep"]
PlanSource = Literal["from_request", "rebuilt", "mixed"]
Origin = Literal["model", "template", "unknown"]
# How far the export's own hash seal could be checked. Never a refusal: a missing baseline or a changed Brief is
# researched anyway and the report says so (D-156).
InputBinding = Literal["verified", "unverified_no_baseline", "unverified_hash_mismatch"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Idea(Frozen):
    summary: str = ""
    category: str = ""
    subcategory: str = ""
    business_model: str = ""


class Country(Frozen):
    """One country the app is used in, as the person named it. ``code`` is lowercase alpha-2 when known."""

    name: str
    code: str | None = None


class Geography(Frozen):
    """Where the app is used. ``cities`` and ``region`` are as stated; ``country_code`` may be inferred.

    ``country`` and ``country_code`` are the PRIMARY country (the first one named); ``countries`` lists every country
    the Brief names ("Mexico, Guatemala and Costa Rica" is three), so a single-country input is a list of one."""

    country: str | None = None
    country_code: str | None = None  # lowercase ISO 3166-1 alpha-2, when known
    countries: tuple[Country, ...] = ()
    cities: tuple[str, ...] = ()
    region: str | None = None
    uncertain: bool = (
        False  # a weak Locked value (a city from a data-residency quote): trust the country
    )
    uncertain_reason: str | None = None
    tier: Tier = "default"


class Locales(Frozen):
    codes: tuple[
        str, ...
    ] = ()  # the app's languages, as Compass sent them (Hinglish arrives as hi)
    search_languages: tuple[str, ...] = ()
    non_latin: tuple[
        str, ...
    ] = ()  # codes whose script is not Latin (need font and script research)
    notes: tuple[str, ...] = ()  # flagged mismatches ("hi sent for Hinglish"), never guessed away


class Feature(Frozen):
    id: str
    name: str
    intent: str = ""
    priority: str = "nice"  # must | nice | later (ranks targets only; later does not mean defer)
    scope_status: str = "in"
    tier: Tier = "default"
    duplicate_of: str | None = None  # the id kept when two names mean one thing


class Role(Frozen):
    id: str
    name: str
    goal: str = ""
    tier: Tier = "default"
    duplicate_of: str | None = None
    is_group: bool = False  # a broad group name ("drivers") next to a specific role ("driver")
    is_app_name: bool = False  # a role that is really an app name ("... screens")


class App(Frozen):
    id: str
    name: str
    purpose: str = ""
    tier: Tier = "default"
    is_real: bool = (
        True  # False for a placeholder ("One app for everybody") or an app-name duplicate of a role
    )


class Module(Frozen):
    id: str
    name: str
    apps: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


class Entity(Frozen):
    id: str
    key: str
    name: str
    module: str | None = None


class Flow(Frozen):
    id: str
    name: str
    role: str | None = None
    features: tuple[str, ...] = ()


class Integration(Frozen):
    name: str
    preference: str = ""
    tier: Tier = "default"


class Reference(Frozen):
    label: str
    tier: Tier = "default"
    own_brand: bool = (
        False  # equal to (or inseparable from) the user's own brand: never searched alone
    )
    ambiguous: bool = False  # cannot be told from the user's own brand


class Fact(Frozen):
    id: str
    kind: str | None = None  # rule, compliance, integration_detail, nfr, ...
    type: str = ""
    statement: str
    topics: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()
    tier: Tier = "default"
    has_quote: bool = True  # a Locked fact with no verified words must not support anything Locked
    is_template: bool = False  # "Kind of app: ..." style filler


class Request(Frozen):
    id: str
    topic: str
    question: str = ""
    decision_unlocked: str = ""
    entity: str = ""
    geography: str = ""
    source_languages: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    prefer_sources: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    priority: str = "medium"
    shown_to_user_as: str = ""
    origin: Origin = "unknown"  # model | template (derived when Compass does not say)
    thin: bool = False
    reasons: tuple[str, ...] = ()  # why it looks like template or thin text


class Failure(Frozen):
    call: str
    reason: str  # timeout | zero_token | fallback | refused


class RequestCounts(Frozen):
    trusted: int = 0
    template: int = 0
    thin: int = 0


class InputQuality(Frozen):
    requests: RequestCounts = RequestCounts()
    plan_source: PlanSource = "from_request"
    degraded: bool = False
    failing_probes: tuple[str, ...] = ()
    structure_source: Literal["model", "mixed", "fallback", "unknown"] = "unknown"
    geo_uncertain: bool = False
    failures: tuple[
        Failure, ...
    ] = ()  # recomputed from spend.by_call (Compass fallback_count undercounts)
    flags: tuple[
        str, ...
    ] = ()  # derived-and-flagged notes (missing trust fields, junk tokens, ...)
    input_binding: InputBinding = (
        "verified"  # the hash seal of the export; shown in the preview and the report
    )
    compass_format: int | None = None  # the export's own format version, when it said one


class LoadComponents(Frozen):
    features: int = 0
    facts: int = 0
    apps: int = 0
    sensitive: int = 0
    references: int = 0


class LoadInfo(Frozen):
    score: int
    load_class: LoadClass
    components: LoadComponents


class SearchHints(Frozen):
    disambiguated_names: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    local_competitors: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


class FocusTopic(Frozen):
    topic: str
    about: tuple[str, ...] = ()


class PrivateTerm(Frozen):
    """Something that must never be sent to a search provider. ``kind`` says why (brand, entity, phone, email,
    address, sensitive, person); ``text`` is the value as the Brief wrote it. Never logged, never shown."""

    kind: str
    text: str


class PrivateTerms(Frozen):
    terms: tuple[PrivateTerm, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.terms)


class ResearchInput(Frozen):
    program_id: str
    session_id: str
    brief_version: int
    brief_hash: str
    idea: Idea
    geography: Geography
    locales: Locales
    audience: tuple[str, ...] = ()
    features: tuple[Feature, ...] = ()
    roles: tuple[Role, ...] = ()
    apps: tuple[App, ...] = ()
    modules: tuple[Module, ...] = ()
    entities: tuple[Entity, ...] = ()
    flows: tuple[Flow, ...] = ()
    integrations: tuple[Integration, ...] = ()
    references: tuple[Reference, ...] = ()
    glossary: tuple[str, ...] = ()
    facts: tuple[Fact, ...] = ()
    requests: tuple[Request, ...] = ()
    declined: tuple[str, ...] = ()
    hints: SearchHints = SearchHints()
    focus: tuple[FocusTopic, ...] = ()
    sensitive_flags: tuple[str, ...] = ()
    own_brand_terms: tuple[str, ...] = ()
    private: PrivateTerms = PrivateTerms()  # what a query may never contain (core/privacy.py)
    quality: InputQuality = InputQuality()
    load: LoadInfo | None = None

    def feature_ids(self) -> set[str]:
        return {f.id for f in self.features}
