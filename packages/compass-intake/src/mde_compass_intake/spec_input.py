"""``SpecInput``: everything the Spec agent reads from a Compass export, in one typed, trust-graded object.

Built by ``reader.read_export``; never by hand. The shape follows doc 15 section 3 (``docs/architecture-v3/15-spec-agent-design.md``):

* every value that Spec may lean on carries a :class:`Trust` (``locked`` / ``default`` / ``open``). Compass's own tier is
  NOT taken at its word ("approval is not review"): a feature is ``locked`` only when a verified quote supports it, a
  fact, geography, locale or style value only with a quote and a confidence of at least 0.7, the structure never;
* cleaning is recorded, not silent: duplicates keep their rows with ``duplicate_of`` set and a :class:`Merge` is listed;
  nothing is dropped;
* what the reader had to assume (a role derived from the audience, a default locale) is in ``assumptions`` and makes
  ``thin_input`` true; drift (an unknown format version, a renamed key) is a string in ``drift_flags``.

IDs are Compass's own and are carried unchanged (doc 15 section 3.6); IDs the reader mints for a derived role live in
their own namespace (``ROL-S001``).
"""

from __future__ import annotations

from typing import Literal

from mde_compass_intake.models import Country, Failure, Frozen, Idea, InputBinding, Tier

Reason = Literal["user", "capacity", "unknown"]


class Trust(Frozen):
    """How far one value can be trusted, graded by the reader (doc 15 section 3.4)."""

    grade: Tier = "default"
    confidence: float | None = None
    assumed: bool = (
        False  # Compass says it guessed (a pattern, an assumption), or the reader had to
    )
    verified_quote: bool = (
        False  # a quote of the person's own words supports it and was found in the record
    )
    quote_ids: tuple[str, ...] = ()  # the Brief ids (``FCT-006``) of the verified quotes


class Assumption(Frozen):
    """Something the reader filled in because the Brief did not say (a thin input). Listed at the Spec gates."""

    field: str
    value: str
    reason: str


class Merge(Frozen):
    """A cleaning step that folded one record into another (singular and plural, role against app, duplicate name)."""

    kind: Literal["feature", "role", "app"]
    from_id: str
    into_id: str
    reason: str


class NameClash(Frozen):
    """One name used both as a role and as an app ("Manager side")."""

    name: str
    role_id: str
    app_id: str | None = None


class Geography(Frozen):
    city: str = ""
    region: str = ""
    country: str = ""
    country_code: str | None = None
    countries: tuple[Country, ...] = ()
    uncertain: bool = True
    uncertain_reason: str | None = None
    trust: Trust = Trust()


class Locales(Frozen):
    codes: tuple[str, ...] = ()
    search_languages: tuple[str, ...] = ()
    source: str = "none"  # view | experience | meta | search_hints | default | none
    parsed_from_text: bool = False
    non_latin: tuple[str, ...] = ()
    rtl: tuple[str, ...] = ()
    trust: Trust = Trust()


class Setting(Frozen):
    """A stated or assumed setting: tone words, density, devices, sign-in methods, account model, brand."""

    values: tuple[str, ...] = ()
    trust: Trust = Trust()


class Feature(Frozen):
    id: str
    name: str
    intent: str = ""
    scope_status: str = "in"
    priority: str = "nice"
    tier: Tier = "default"  # Compass's tier, kept for the record; ``trust`` is the grade Spec uses
    apps: tuple[str, ...] = ()
    trust: Trust = Trust()  # existence: locked only with a verified quote
    user_evidenced: bool = (
        False  # a verified quote supports it (also true for a representative of merged duplicates)
    )
    quote_ids: tuple[str, ...] = ()
    priority_trust: Trust = Trust()  # default unless the user ranked it
    deferred_by_user: bool = (
        False  # a user-evidenced ``later`` (never relabelled, doc 15 SPC-IN-21)
    )
    duplicate_of: str | None = None
    alias_ids: tuple[str, ...] = ()  # ids folded into this one


class Role(Frozen):
    id: str
    name: str
    goal: str = ""
    tier: Tier = "default"
    apps: tuple[str, ...] = ()
    trust: Trust = (
        Trust()
    )  # locked only after cleaning (not a duplicate, not an app name, not junk)
    duplicate_of: str | None = None
    alias_ids: tuple[str, ...] = ()
    is_app_name: bool = False
    junk: bool = False
    assumed: bool = False  # derived by the reader, not in the Brief


class App(Frozen):
    id: str
    name: str
    purpose: str = ""
    surface: str = ""
    roles: tuple[str, ...] = ()
    tier: Tier = "default"
    trust: Trust = Trust()
    duplicate_of: str | None = None
    is_real: bool = True  # False for a name that is really a role
    junk: bool = False


class Module(Frozen):
    id: str
    name: str
    kind: str = ""
    purpose: str = ""
    apps: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    entry: str = ""


class ScreenUse(Frozen):
    entity: str
    use: str = ""


class Screen(Frozen):
    id: str
    name: str
    purpose: str = ""
    module: str = ""
    apps: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    serves: tuple[str, ...] = ()
    entities: tuple[ScreenUse, ...] = ()
    pattern: str = ""
    phase: str = ""


class FlowStep(Frozen):
    screen: str = ""
    action: str = ""


class Flow(Frozen):
    id: str
    name: str
    goal: str = ""
    role: str = ""
    steps: tuple[FlowStep, ...] = ()
    features: tuple[str, ...] = ()
    alternates: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()


class EntityField(Frozen):
    key: str
    name: str = ""
    type: str = ""
    required: bool = False
    ref: str = ""
    values: tuple[str, ...] = ()


class Entity(Frozen):
    id: str
    key: str = ""
    name: str = ""
    purpose: str = ""
    module: str = ""
    fields: tuple[EntityField, ...] = ()
    states: tuple[str, ...] = ()
    access: dict[str, str] = {}


class StructureIssue(Frozen):
    code: str
    severity: str = ""
    ids: tuple[str, ...] = ()
    detail: str = ""


class StructureProposal(Frozen):
    """Compass's structure (screens, flows, entities, who may do what, navigation). A PROPOSAL, graded ``default``."""

    present: bool = False
    source: str = "unknown"  # unknown | fallback | model | mixed (how much of it a model drafted)
    band: str = ""
    grade: Tier = "default"
    screens: tuple[Screen, ...] = ()
    flows: tuple[Flow, ...] = ()
    entities: tuple[Entity, ...] = ()
    role_matrix: dict[str, dict[str, tuple[str, ...]]] = {}
    navigation: tuple[tuple[str, str], ...] = ()
    issues: tuple[StructureIssue, ...] = ()


class Integration(Frozen):
    name: str
    preference: str = ""
    notes: str = ""
    tier: Tier = "default"
    trust: Trust = Trust()


class Fact(Frozen):
    id: str
    kind: str | None = None
    type: str = ""
    statement: str = ""
    topics: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()
    tier: Tier = "default"
    has_quote: bool = False
    trust: Trust = Trust()  # a Locked fact with confidence under 0.7 is graded default


class ExistingSystem(Frozen):
    name: str
    kind: str = ""
    pointer: str = ""


class CompassAssumption(Frozen):
    """An assumption Compass recorded. Always graded ``default`` (doc 15 section 3.4)."""

    id: str
    claim: str = ""
    reason: str = ""
    impact: str = ""
    theme: str = ""
    decision: str = ""


class TrustProfile(Frozen):
    """How far the whole input can be relied on (doc 15 sections 3.4 and 3.5)."""

    degraded: bool = False
    input_binding: InputBinding = "verified"
    probes: tuple[str, ...] = ()  # the Compass probes that failed (``design``, ``research``, ...)
    fallback_count: int = 0
    failures: tuple[Failure, ...] = ()
    structure_source: str = "unknown"
    readiness: dict[
        str, str
    ] = {}  # Compass's own section status (``pass`` | ``gaps``): informative only
    weak_inputs: bool = (
        False  # degraded, or the approval cannot be verified: the G2 summary leads with it
    )


class SpecInput(Frozen):
    program_id: str = ""
    session_id: str = ""
    brief_version: int = 0
    brief_hash: str = ""
    input_binding: InputBinding = "verified"
    compass_format: int | None = None
    tier: str = "standard"  # quick | standard | thorough: the interview budget the person chose
    size_band: str = "medium"  # small | medium | large
    size_band_source: str = "tier"

    idea: Idea = Idea()
    idea_trust: Trust = Trust()
    audience: tuple[str, ...] = ()
    geography: Geography = Geography()
    locales: Locales = Locales()
    tone: Setting = Setting()
    density: Setting = Setting()
    devices: Setting = Setting()
    auth_methods: Setting = Setting()
    account_model: Setting = Setting()
    brand: Setting = Setting()

    features: tuple[Feature, ...] = ()
    roles: tuple[Role, ...] = ()
    apps: tuple[App, ...] = ()
    name_clashes: tuple[NameClash, ...] = ()
    modules: tuple[Module, ...] = ()
    structure: StructureProposal = StructureProposal()
    integrations: tuple[Integration, ...] = ()
    domain_facts: tuple[Fact, ...] = ()
    rules_and_numbers: tuple[Fact, ...] = ()
    sensitive_data: tuple[str, ...] = ()
    existing_systems: tuple[ExistingSystem, ...] = ()
    compass_assumptions: tuple[CompassAssumption, ...] = ()
    glossary: tuple[str, ...] = ()
    declined: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    trust: TrustProfile = TrustProfile()
    merges: tuple[Merge, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    thin_input: bool = False
    flags: tuple[
        str, ...
    ] = ()  # the intake flags Research also reads (compass_format_2, geo_uncertain, ...)
    drift_flags: tuple[
        str, ...
    ] = ()  # unknown version, missing or empty expected field, changed enum, size jump
    cleaning_flags: tuple[
        str, ...
    ] = ()  # duplicate or junk roles, duplicate features, mostly-nice priorities

    def in_scope_features(self) -> tuple[Feature, ...]:
        """Features that are neither a duplicate nor out of scope."""
        return tuple(f for f in self.features if f.duplicate_of is None and f.scope_status != "out")

    def clean_roles(self) -> tuple[Role, ...]:
        """Roles that are a real person: not a duplicate, not an app's name, not junk (derived ones included)."""
        return tuple(
            r for r in self.roles if r.duplicate_of is None and not r.is_app_name and not r.junk
        )
