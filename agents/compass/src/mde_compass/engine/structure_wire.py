"""Wire models of the three structure calls (ADR 0017): ``cmp.draft_outline``, ``cmp.draft_module`` and
``cmp.review_structure``. They live in ``engine`` (not in a call folder) because the engine's ports speak them and the
three call folders, the floor and the controller all share them; the call folders re-export what they own.
Strict-schema-safe: flat, every field required, no open objects (a map is a list of pairs).

Records are named by REF inside one call: a real id (``FTR-004``, ``MOD-003``) for something that already exists, a short
local ref (``m1``, ``s7``, ``e2``) for something new. Code (``engine/structure_draft``) resolves refs, allocates the real
ids and checks every pointer; the model never mints an id.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_compass.catalog.size_bands import SizeBand


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BandWire(Wire):
    """The size band as numbers the drafter aims for (guidance: fewer is right for a simple app)."""

    id: str  # small | medium | large
    screens_low: int
    screens_high: int
    open_upper: bool  # true: "screens_high or more"
    modules_low: int
    modules_high: int
    flows_low: int
    flows_high: int
    entities_low: int
    entities_high: int
    must_feature_screens_min: int
    module_screens_min: int


class FeatureWire(Wire):
    id: str  # FTR-###
    name: str
    intent: str
    priority: str  # must | nice | later
    scope_status: str  # in | later | out | ...
    family: str  # a catalog family id, or ""
    apps: list[str]  # APP-### it belongs to; empty = all


class RoleWire(Wire):
    id: str  # ROL-###
    name: str
    goal: str


class AppWire(Wire):
    id: str  # APP-###
    name: str
    surface: str


class PinnedWire(Wire):
    """A record the person accepted or wrote: it stays whatever is drafted (code keeps it; the model works around it)."""

    kind: str  # module | screen | entity | flow
    id: str  # MOD-### ...
    name: str
    purpose: str


def band_wire(band: SizeBand) -> BandWire:
    return BandWire(
        id=band.id,
        screens_low=band.screens[0],
        screens_high=band.screens[1],
        open_upper=band.open_upper,
        modules_low=band.modules[0],
        modules_high=band.modules[1],
        flows_low=band.flows[0],
        flows_high=band.flows[1],
        entities_low=band.entities[0],
        entities_high=band.entities[1],
        must_feature_screens_min=band.must_feature_screens_min,
        module_screens_min=band.module_screens_min,
    )


# --------------------------------------------------------------------------------------- draft_outline: output


class ModuleOut(Wire):
    ref: str  # an existing MOD-### to keep, or a new local ref (m1, m2 ...)
    kind: str  # "" for an ordinary module; "account" or "support:<id>" for a standard one (a hint the floor reads)
    name: str
    purpose: str
    apps: list[str]  # APP-###
    features: list[str]  # FTR-### the module delivers
    roles: list[str]  # ROL-### who works in it
    screen_target: int  # how many screens the module should have (guidance)


class EntityOut(Wire):
    ref: str  # an existing ENT-### to keep, or a new local ref (e1, e2 ...)
    name: str
    purpose: str
    owner_module: str  # a module ref whose call writes the fields


class JourneyOut(Wire):
    ref: str  # j1, j2 ... or an existing FLW-###
    name: str
    role: str  # ROL-###
    goal: str
    legs: list[str]  # module refs in order; a module may repeat
    owner_module: str  # the module ref that writes the acceptance lines


class OutlineOutput(Wire):
    modules: list[ModuleOut]
    entities: list[EntityOut]
    journeys: list[JourneyOut]


# ---------------------------------------------------------------------------------------- draft_module: input


class ModuleCtx(Wire):
    """One module of the group, as the outline decided it."""

    ref: str
    existing_id: str  # the MOD-### it already has, or ""
    kind: str  # as ModuleOut.kind
    name: str
    purpose: str
    apps: list[str]
    features: list[str]
    roles: list[str]
    screen_target: int


class EntityCtx(Wire):
    ref: str
    name: str
    purpose: str
    owner_module: str
    mine: bool  # true: this call writes its fields, states and access


class LegCtx(Wire):
    index: int  # the position of the leg in the journey
    module: str  # module ref
    here: bool  # true: a module of this group writes this leg's steps


class JourneyCtx(Wire):
    ref: str
    name: str
    role: str
    goal: str
    legs: list[LegCtx]
    owner: bool  # true: this group writes the acceptance lines and alternates


class ExistingScreen(Wire):
    id: str  # SCR-###
    module: str  # module ref
    name: str
    purpose: str
    pinned: bool  # accepted or written by the person: keep it exactly


# --------------------------------------------------------------------------------------- draft_module: output
# FLAT on purpose: a strict output schema may nest at most five levels (objects and arrays each count one), so the
# records are separate lists that point at each other by ref, not trees.


class ScreenOut(Wire):
    ref: str  # an existing SCR-### to keep or edit, or a new local ref (s1, s2 ...)
    module: str  # the module ref it belongs to
    name: str
    purpose: str
    pattern: str  # one of the allowed patterns
    apps: list[str]
    roles: list[str]  # ROL-### who can open it
    serves: list[str]  # FTR-### it is part of


class UseOut(Wire):
    """What a screen does with an entity: show it, create it or edit it."""

    screen: str  # screen ref
    entity: str  # entity ref
    use: str  # view | create | edit


class EntryOut(Wire):
    module: str  # module ref
    screen: str  # the screen ref the module opens on


class StepOut(Wire):
    screen: str  # screen ref
    action: str


class SegmentOut(Wire):
    journey: str  # journey ref
    leg: int  # the LegCtx.index it writes
    module: str  # the module ref whose screens the steps use
    steps: list[StepOut]


class EntityDraft(Wire):
    ref: str  # entity ref
    key: str  # ascii snake_case
    states: list[str]


class FieldOut(Wire):
    entity: str  # entity ref
    key: str  # ascii snake_case
    name: str
    type: str  # one of the allowed field types
    required: bool
    ref: str  # entity ref when type is ref, else ""
    values: list[str]  # allowed values when type is enum, else []


class AccessOut(Wire):
    entity: str  # entity ref
    role: str  # ROL-###
    level: str  # none | view | create | edit | manage


class JourneyDetail(Wire):
    ref: str
    acceptance: list[str]
    alternates: list[str]


class ModuleOutput(Wire):
    screens: list[ScreenOut]
    uses: list[UseOut]
    entries: list[EntryOut]
    segments: list[SegmentOut]
    entities: list[EntityDraft]  # the entities this call owns
    fields: list[FieldOut]
    access: list[AccessOut]
    journeys: list[JourneyDetail]  # acceptance and alternates for the journeys this call owns


# -------------------------------------------------------------------------------------- review_structure


class FindingOut(Wire):
    code: str  # contradiction | unrealistic | missing | inconsistent_permission
    ids: list[str]  # the structure ids it is about (MOD/SCR/FLW/ENT/ROL/FTR-###)
    fact_id: str  # the FCT-### it rests on, or ""
    note: str  # one short plain sentence


FINDING_CODES: tuple[str, ...] = (
    "contradiction",
    "unrealistic",
    "missing",
    "inconsistent_permission",
)
USES: tuple[str, ...] = ("view", "create", "edit")
LEVELS: tuple[str, ...] = ("none", "view", "create", "edit", "manage")


# ------------------------------------------------------------------------------------------ the three inputs


class OutlineInput(Wire):
    """Everything ``cmp.draft_outline`` reads. All of it is data: the person's words, documents and earlier drafts."""

    language: str  # "en" | "hi" | "hi-Latn": names and purposes are written in it
    band: BandWire
    project_card: str  # idea one-liner, category, geography, audience, language
    features: list[FeatureWire]
    roles: list[RoleWire]
    apps: list[AppWire]
    integrations: list[str]
    auth_methods: list[str]  # phone_otp | email_password | no_sign_in
    org: str  # the company's own answers (identity, approvers, regions ...) as text, or ""
    fact_digest: str  # one line per active fact, within the budget
    seeds: list[
        str
    ]  # "screen|flow|entity FCT-###: statement" the person or a document said: include them
    pinned: list[PinnedWire]
    instruction: str  # the person's own words for a regeneration, or ""


class ModuleInput(Wire):
    """Everything ``cmp.draft_module`` reads for ONE group of modules."""

    language: str
    band: BandWire
    project_card: str
    modules: list[ModuleCtx]  # the group
    other_modules: list[
        str
    ]  # names of the modules outside the group (so screens do not repeat theirs)
    entities: list[EntityCtx]  # the whole vocabulary; ``mine`` marks the ones this call writes
    roles: list[RoleWire]
    apps: list[AppWire]
    features: list[FeatureWire]  # the ones these modules deliver
    journeys: list[JourneyCtx]  # the ones that touch the group
    existing: list[
        ExistingScreen
    ]  # screens that already exist (match by id, never mint a new id for them)
    auth_methods: list[str]
    facts: str  # the detail facts routed to this group, one line each
    instruction: str  # the person's own words for a regeneration, or ""
    issues: list[str]  # machine-written problems to fix (repair) or thin spots to cover (expansion)
    patterns: list[str]  # the allowed screen patterns
    field_types: list[str]  # the allowed entity field types


class ReviewInput(Wire):
    """Everything ``cmp.review_structure`` reads: the structure as lines, the facts, what roles may do."""

    language: str
    band: str  # small | medium | large
    outline: str  # the structure: one line per module, screen, flow and entity, with ids
    facts: str  # the detail facts, one line each, with FCT ids
    permissions: str  # each role's own words about what it may do


class ReviewOutput(Wire):
    findings: list[FindingOut]
