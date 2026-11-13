"""The Spec Graph: typed nodes with tiers, provenance and stable ids (doc 15 5.1, doc 04 section 2).

The graph is canonical; the pack is its deterministic render. Closed vocabularies that are DATA
(shapes, intents, state kinds, screen types) are plain strings here and are checked by validators
against the versioned data files, so a vocabulary change is a data change, not a code change.
No layout field exists anywhere in this model (doc 15 2.2): that is how freedom is enforced.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .canon import canonical_json, sha256_json

Tier = Literal["locked", "default", "open"]
Importance = Literal["primary", "supporting", "optional"]
ActionImportance = Literal["primary", "secondary"]
Status = Literal["in", "later", "out"]
LaterReason = Literal["user", "capacity", "first_release"]
Voice = Literal["must", "usually", "could"]
ProvKind = Literal["user", "compass", "research", "library", "policy", "code", "model", "assumed"]
Surface = Literal["phone", "tablet", "web"]


class Node(BaseModel):
    """Base envelope. Strict: an unknown field is an error (a layout field cannot sneak in)."""

    model_config = ConfigDict(extra="forbid")


class Prov(Node):
    """Where a fact came from. `policy:<sheet id>@<date>` is policy evidence (V-33)."""

    kind: ProvKind
    ref: str = ""  # Brief id, research item id, library id, policy id@date
    quote_id: str | None = None


class Envelope(Node):
    id: str
    tier: Tier = "default"
    assumed: bool = False
    degraded: bool = False
    status: Status = "in"
    later_reason: LaterReason | None = None
    provenance: list[Prov] = Field(default_factory=list)
    input_hash: str | None = None  # hash of the inputs this node was written from (doc 15 10.5)
    aliases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- scope and people


class Feature(Envelope):
    name: str
    intent: str = ""
    user_priority: Literal["must", "should", "nice", "unranked"] = "unranked"
    user_evidenced: bool = False  # a verified quote supports its existence
    depends_on: list[str] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    outline: str = ""  # kept for `later` features (V-19: no screen nodes)
    consent_id: str | None = None  # G1.5 consent record when a must is deferred (I-4)
    capability: str | None = None  # capability sheet id, e.g. cap.nfc
    capability_tier: Literal["A", "B", "C"] | None = None


class Role(Envelope):
    name: str
    goal: str = ""
    level: int | None = None
    app_ids: list[str] = Field(default_factory=list)
    home_screen: str | None = None


class App(Envelope):
    name: str
    surface: Surface = "phone"
    role_ids: list[str] = Field(default_factory=list)
    module_ids: list[str] = Field(default_factory=list)
    scope_cap: int | None = None


class Module(Envelope):
    name: str
    purpose: str = ""
    app_ids: list[str] = Field(default_factory=list)
    shared: bool = False  # specified once, used by several apps (doc 19 2.3)
    entity_ids: list[str] = Field(default_factory=list)
    screen_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- data and contract


class FieldDef(Node):
    key: str
    type: str  # text number money date phone email choice file location code secret quantity toggle id enum list ref
    required: bool = False
    enum_values: list[str] = Field(default_factory=list)
    ref_entity: str | None = None
    derived: str | None = None  # a rule id (RUL-..), never an expression string
    constraint: str = ""
    media: bool = False  # drives data-bound asset needs by code


class Transition(Node):
    from_state: str
    to_state: str
    roles: list[str] = Field(default_factory=list)
    on: str = ""  # event or operation id


class Lifecycle(Node):
    states: list[str] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    initial: str | None = None


class AccessRow(Node):
    role_id: str
    actions: list[Literal["none", "view", "create", "edit", "delete", "manage"]]


class Entity(Envelope):
    name: str
    owner_module: str | None = None
    klass: Literal["primary", "secondary", "join", "log"] = "secondary"
    fields: list[FieldDef] = Field(default_factory=list)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    access: list[AccessRow] = Field(default_factory=list)
    typical_count: int | None = None
    max_count: int | None = None
    later: bool = False  # entity needed only by `later` features (whole-scope model, doc 15 4.5)


class OpError(Node):
    code: str
    recovery: str = ""  # stay | retry | go SCR-.. | offline | ...


class Operation(Envelope):
    name: str
    entity: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = ""
    kind: Literal["crud", "custom"] = "crud"
    request: list[FieldDef] = Field(default_factory=list)
    response: list[FieldDef] = Field(default_factory=list)
    errors: list[OpError] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    idempotent: bool = False
    paged: bool = False
    app_tags: list[str] = Field(default_factory=list)


class Rule(Envelope):
    subject: str
    statement: str
    condition: str = ""
    value: str | None = None  # numeric value with unit, checked by V-45
    unit: str | None = None
    confirm: bool = False
    module_id: str | None = None
    claims_used: list[str] = Field(default_factory=list)


class Integration(Envelope):
    name: str
    need_key: str = ""  # e.g. payments
    mode: Literal["mock", "adapter", "real"] = "mock"
    provider_side_flow: bool = False
    flow: str = ""  # provider-neutral flow
    interruption: str = ""  # interrupted external flow (required for provider-side flows)
    pending_timeout: str = ""  # pending timeouts (required for provider-side flows)
    events: list[str] = Field(default_factory=list)
    mock_behaviour: str = ""
    provider_ids: list[str] = Field(default_factory=list)  # research dossier ids
    claims_used: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- screens


class RouteParam(Node):
    name: str
    type: str
    source: str = ""


class Guard(Node):
    """A structured predicate, never an expression string."""

    pred: Literal["signed_in", "role_has_access", "field_equals", "field_in"]
    entity: str | None = None
    field: str | None = None
    values: list[str] = Field(default_factory=list)


class Route(Node):
    name: str = ""
    path: str = ""
    params: list[RouteParam] = Field(default_factory=list)
    guards: list[Guard] = Field(default_factory=list)


class Region(Node):
    key: str
    what: str
    shape: str
    shape_kind: str | None = None  # the attribute that picks a variant (kind, count, severity)
    attrs: list[str] = Field(default_factory=list)  # "name=value" attributes of the shape
    bind: list[str] = Field(default_factory=list)  # ENT-x.field or OP-x
    importance: Importance = "supporting"
    freshness: str | None = None
    use_block: str | None = None  # BLK-x@depth
    cardinality: str | None = None  # bucket: one few many very_many
    typical: int | None = None
    maximum: int | None = None
    live: Literal["none", "polled", "realtime"] = "none"
    copy_unspecified: bool = False


class Step(Node):
    """One typed step of an action's effect (at most three)."""

    verb: Literal["call", "go", "stay", "external_flow"]
    target: str | None = None  # OP-x, SCR-y, or flow name


class Action(Node):
    key: str
    importance: ActionImportance = "secondary"
    intent: str
    what: str
    precondition: str = ""
    effect: list[Step] = Field(default_factory=list)
    guard: list[Guard] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    flags: list[Literal["optimistic", "offline", "idempotent"]] = Field(default_factory=list)
    unspecified: bool = False  # a card needs an operation Product never wrote (FN-36)


class Input(Node):
    key: str
    type: str
    required: bool = False
    constraint: str = ""
    default: str | None = None
    source: str = ""


class StateSpec(Node):
    kind: str  # loading empty error offline forbidden not_found stale partial success domain:<x>
    message: str
    origin: Literal["library", "model"] = "library"


class Acceptance(Node):
    given: str
    when: str
    then: str


class Usually(Node):
    note: str
    evidence: Literal["official", "corroborated", "single_source", "unverified", "library"]
    item: str | None = None


class Could(Node):
    id: str
    kind: Literal[
        "delight",
        "clarity",
        "speed",
        "trust",
        "personalization",
        "accessibility",
        "platform",
        "alternative",
    ]
    when: str
    effect: str
    basis: str
    basis_kind: Literal["journey_moment", "research_item", "capability_sheet", "library_pattern"]
    assumed: bool = False
    motion: str | None = None
    reduced_motion_alt: str | None = None
    tier: Literal["A", "B", "C"] = "A"


class Screen(Envelope):
    name: str
    app_id: str
    module_id: str | None = None
    screen_type: str
    surface: Surface = "phone"
    role_ids: list[str] = Field(default_factory=list)
    purpose: str = ""
    route: Route = Field(default_factory=Route)
    reached_from: list[str] = Field(default_factory=list)  # SCR-x.key, informational
    leads_to: list[str] = Field(default_factory=list)
    role_conditions: str = ""
    regions: list[Region] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    inputs: list[Input] = Field(default_factory=list)
    states: list[StateSpec] = Field(default_factory=list)
    copy_keys: list[str] = Field(default_factory=list)
    analytics: list[str] = Field(default_factory=list)
    accessibility: str = ""
    acceptance: list[Acceptance] = Field(default_factory=list)
    usually: list[Usually] = Field(default_factory=list)
    could: list[Could] = Field(default_factory=list)
    stories: list[str] = Field(default_factory=list)
    mock_profiles: list[str] = Field(default_factory=list)
    hero_refs: list[str] = Field(default_factory=list)
    confirm: list[str] = Field(default_factory=list)
    claims_used: list[str] = Field(default_factory=list)
    system: bool = False  # from the system set (provenance policy or library)


# ---------------------------------------------------------------- why layer


class Persona(Envelope):
    role_id: str
    goal: str
    context: str = ""
    frequency: str = ""
    skill: str = ""
    pain_points: list[str] = Field(default_factory=list)
    success: str = ""


class Story(Envelope):
    role_id: str
    want: str
    so_that: str
    feature_ids: list[str] = Field(default_factory=list)
    policy_id: str | None = None
    screen_ids: list[str] = Field(default_factory=list)
    voice: Voice = "must"
    acceptance: list[Acceptance] = Field(default_factory=list)


class Stage(Node):
    n: int
    goal: str
    screen_ids: list[str] = Field(default_factory=list)
    action_keys: list[str] = Field(default_factory=list)
    emotion: Literal["calm", "curious", "confident", "anxious", "frustrated", "delighted"] = "calm"
    emotion_assumed: bool = True
    pain: str = ""


class Flow(Envelope):
    name: str
    role_ids: list[str] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    primary: bool = False
    variant: Literal["main", "first_run", "returning", "cross_app", "recovery"] = "main"
    stages: list[Stage] = Field(default_factory=list)
    moments_that_matter: list[int] = Field(default_factory=list)  # stage numbers
    unhappy_paths: list[str] = Field(default_factory=list)
    mock_profile: str = "typical"


class Behavior(Envelope):
    category: str  # session offline errors loading permissions notifications i18n analytics state media ...
    text: str
    applies_to: list[str] = Field(default_factory=list)


class Direction(Envelope):
    name: str
    mood: str
    audience_context: str = ""
    voice_line: str = ""
    ambition: Literal["refined", "expressive", "immersive"] = "expressive"
    motion_personality: str = "calm"
    depth: str = "soft"
    shape: str = "rounded"
    density: str = "comfortable"
    imagery: str = "photo_led"
    type_personality: str = "neutral"
    seed_hue: float | None = None
    seed_colour: str | None = None  # a seed, never a per-screen colour
    brand_locked: bool = (
        False  # the user gave this colour: it stays Locked and the text on it is derived
    )
    delight_budget: int = 2
    dark_stance: str = "supported"
    avoid: list[str] = Field(default_factory=list)
    emotional_goals: list[str] = Field(default_factory=list)
    role_variants: list[str] = Field(
        default_factory=list
    )  # deep: how consumer and admin directions differ
    personality: str = ""


class AssetNeed(Envelope):
    kind: str
    purpose: str
    subject: str
    count: int = 1
    avoid: list[str] = Field(default_factory=list)
    source_policy: Literal["user_upload", "generate", "licensed_stock", "none"] = "generate"
    license_rule: str = ""
    alt_text_rule: str = ""
    fallback: str = ""
    used_by: list[str] = Field(default_factory=list)
    bound: bool = False  # data-bound needs are made by code


class HeroRecord(Node):
    id: str  # stable, same in every app
    entity: str
    state: str | None = None
    relations: dict[str, str] = Field(default_factory=dict)  # field key -> hero id (set by code)
    content: dict[str, str] = Field(default_factory=dict)  # filled by MOCK-HERO
    image_brief: str | None = None
    filled: bool = False
    synthetic: bool = True


class GraphMeta(Node):
    version: int = 1
    program_id: str = ""
    profile: Literal["compact", "standard", "deep"] = "standard"
    source_language: str = "en"
    country: str = ""  # ISO country of the main market; locale pools follow it when set
    languages: list[str] = Field(default_factory=lambda: ["en"])
    frozen: bool = False
    inputs_hash: str = ""
    facts: list[str] = Field(
        default_factory=list
    )  # predicate facts that hold (vocab/predicates.yaml)
    degraded_locales: list[str] = Field(
        default_factory=list
    )  # locales whose copy fell back (flagged at G2)


NODE_TYPES: dict[str, type[Envelope]] = {
    "features": Feature,
    "roles": Role,
    "apps": App,
    "modules": Module,
    "entities": Entity,
    "operations": Operation,
    "rules": Rule,
    "integrations": Integration,
    "screens": Screen,
    "personas": Persona,
    "stories": Story,
    "flows": Flow,
    "behaviors": Behavior,
    "directions": Direction,
    "assets": AssetNeed,
}


class SpecGraph(Node):
    """Everything Spec knows, by kind, keyed by id. Lists are written in id order (deterministic)."""

    meta: GraphMeta = Field(default_factory=GraphMeta)
    features: dict[str, Feature] = Field(default_factory=dict)
    roles: dict[str, Role] = Field(default_factory=dict)
    apps: dict[str, App] = Field(default_factory=dict)
    modules: dict[str, Module] = Field(default_factory=dict)
    entities: dict[str, Entity] = Field(default_factory=dict)
    operations: dict[str, Operation] = Field(default_factory=dict)
    rules: dict[str, Rule] = Field(default_factory=dict)
    integrations: dict[str, Integration] = Field(default_factory=dict)
    screens: dict[str, Screen] = Field(default_factory=dict)
    personas: dict[str, Persona] = Field(default_factory=dict)
    stories: dict[str, Story] = Field(default_factory=dict)
    flows: dict[str, Flow] = Field(default_factory=dict)
    behaviors: dict[str, Behavior] = Field(default_factory=dict)
    directions: dict[str, Direction] = Field(default_factory=dict)
    assets: dict[str, AssetNeed] = Field(default_factory=dict)
    hero: dict[str, HeroRecord] = Field(default_factory=dict)
    glossary: dict[str, str] = Field(default_factory=dict)  # term -> source-language term
    locale_copy: dict[str, dict[str, str]] = Field(default_factory=dict)  # locale -> key -> text
    tokens: dict[str, Any] = Field(
        default_factory=dict
    )  # direction id -> DTCG tree (built by code)

    # -- access ---------------------------------------------------------------------------

    def all_nodes(self) -> list[Envelope]:
        out: list[Envelope] = []
        for kind in NODE_TYPES:
            out.extend(getattr(self, kind).values())
        return out

    def find(self, node_id: str) -> Envelope | None:
        for kind in NODE_TYPES:
            node = getattr(self, kind).get(node_id)
            if node is not None:
                return node
        return None

    def add(self, kind: str, node: Envelope) -> None:
        table: dict[str, Envelope] = getattr(self, kind)
        if node.id in table:
            raise ValueError(
                f"duplicate id {node.id} in {kind}"
            )  # duplicates are never fixed by code
        table[node.id] = node

    # -- deterministic output -------------------------------------------------------------

    def to_canonical(self) -> dict[str, Any]:
        """Plain dict, tables sorted by id; `copy` and `tokens` are part of the content."""
        data = self.model_dump(mode="json", exclude_none=False)
        return _sort_tables(data)

    def canonical_text(self) -> str:
        return canonical_json(self.to_canonical())

    def content_hash(self) -> str:
        """What Gate 2 binds. Route, model and consumer identity are not in the graph (doc 18 4.1).

        COULD entries are Open and outside the hash (doc 17 1.4): they are removed before hashing.
        """
        data = self.to_canonical()
        for s in data["screens"].values():
            s["could"] = []
        return sha256_json(data)


def _sort_tables(data: dict[str, Any]) -> dict[str, Any]:
    # JSON object key order is fixed by sort_keys at serialisation; ids are already dict keys.
    return data
