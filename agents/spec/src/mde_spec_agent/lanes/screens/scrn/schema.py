"""Input and output of SCRN. The OUTPUT is the portable subset (ADR 0014, doc 18 4.4): closed records in flat arrays, no
open maps, no optional fields beyond the few nullable ones listed here (13 in all, under the 16 of the profiles), each
record names its screen. Typed steps, guards and route parameters travel as short strings that code parses and checks.
The model never writes layout, widgets, positions, colours or motion choices, and never writes a key of copy: it gives
the TEXT for keys that code mints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput

Importance = Literal["primary", "supporting", "optional"]
ActionImportance = Literal["primary", "secondary"]
Live = Literal["none", "polled", "realtime"]
Evidence = Literal["official", "corroborated", "single_source", "unverified", "library"]
CouldKind = Literal[
    "delight", "clarity", "speed", "trust", "personalization", "accessibility", "platform"
]
BasisKind = Literal["journey_moment", "research_item", "capability_sheet", "library_pattern"]
Tier = Literal["A", "B", "C"]
Flag = Literal["optimistic", "offline", "idempotent"]
CopyFor = Literal["title", "action", "input", "region"]


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- the records (also the baseline inside a slot)


class CardOut(W):
    screen: str
    purpose: str  # at most two sentences
    params: list[str]  # "order_id:id:ENT-006.id" (name:type:source)
    guards: list[
        str
    ]  # signed_in | role_has_access | field_equals ENT-1.status paid | field_in ENT-1.status a|b
    role_conditions: str | None
    reached_from: list[str]  # "SCR-013.pay_order"
    leads_to: list[str]  # screen ids
    accessibility: str | None
    analytics: list[str]  # event names
    mock_profiles: list[str]
    mock_states: list[str]  # "ENT-006:payment_pending" (entity and lifecycle state)
    confirm: list[str]


class RegionOut(W):
    screen: str
    key: str
    what: str  # one sentence: what the person must be able to know here
    shape: str
    shape_kind: str | None
    attrs: list[str]  # "name=value"
    bind: list[str]  # ENT-x.field or OP-x
    importance: Importance
    freshness: str | None
    cardinality: str | None
    typical: int | None
    maximum: int | None
    live: Live


class ActionOut(W):
    screen: str
    key: str
    importance: ActionImportance
    intent: str
    what: str
    precondition: str | None
    effect: list[str]  # at most three: call OP-x | go SCR-y | stay | external_flow name
    guard: list[str]
    errors: list[str]
    flags: list[Flag]
    unspecified: bool  # true only when the operation it needs does not exist (then effect is empty)


class InputOut(W):
    screen: str
    key: str
    type: str
    required: bool
    constraint: str | None
    default: str | None
    source: str


class AcceptOut(W):
    screen: str
    given: str
    when: str
    then: str


class UsuallyOut(W):
    screen: str
    note: str  # descriptive, never an instruction
    evidence: Evidence
    item: str | None


class CouldOut(W):
    screen: str
    kind: CouldKind
    when: str
    effect: str  # what it does for the person, never how it looks
    basis: str
    basis_kind: BasisKind
    assumed: bool
    motion: str | None
    reduced_motion_alt: str | None
    tier: Tier


class CopyOut(W):
    screen: str
    target: CopyFor
    ref: str  # the action key, input key or region key (the title uses "title")
    text: str


class ScrnOut(W):
    cards: list[CardOut]
    regions: list[RegionOut]
    actions: list[ActionOut]
    inputs: list[InputOut]
    acceptance: list[
        AcceptOut
    ]  # only the screen-specific scenarios the library does not already hold
    usually: list[UsuallyOut]
    could: list[CouldOut]
    texts: list[CopyOut]


# ---------------------------------------------------------------- the input


class EntIn(W):
    id: str
    name: str
    klass: str
    status_field: str | None
    states: list[str]
    fields: list[str]  # "key:type" or "key:type:enum_a|enum_b"
    access: str  # what this screen's roles may do: "view" / "create" / ...
    typical: int | None
    maximum: int | None


class OpIn(W):
    id: str
    name: str
    entity: str | None
    method: str
    errors: list[str]
    idempotent: bool
    from_states: list[str]  # lifecycle states this operation leaves (a precondition and a guard)


class MomentIn(W):
    flow: str
    flow_name: str
    step: int
    goal: str
    emotion: str
    assumed: bool
    primary: bool
    matters: bool  # a moment that matters (a signature moment when the journey is primary)


class NormIn(W):
    id: str
    evidence: str
    label: str


class PatternIn(W):
    id: str
    moment: str
    effect: str
    tier: str
    reduced_motion_alternative: str
    motion: str | None


class LibAcceptIn(W):
    given: str
    when: str
    then: str


class SlotIn(W):
    id: str
    name: str
    screen_type: str
    app: str
    module: str
    surface: str
    roles: list[str]  # "ROL-001 Buyer"
    features: list[str]  # "FTR-001 Book a slot: intent [priority]"
    route_path: str
    entities: list[EntIn]
    operations: list[OpIn]
    known_screens: list[str]  # "SCR-015 Payment result (confirmation)": where this screen may lead
    flows: list[MomentIn]
    integrations: list[str]
    confirm_rules: list[str]
    norms: list[NormIn]
    delight: list[PatternIn]
    library_states: list[str]  # code instantiates these: never write them
    library_acceptance: list[LibAcceptIn]  # already present: write only extra scenarios
    extra_acceptance_max: int
    could_max: int
    usually_max: int
    mock_state_options: list[str]
    mock_profile_options: list[str]
    card: CardOut  # the baseline card written by code: improve it, keep what is right
    regions: list[RegionOut]
    actions: list[ActionOut]
    inputs: list[InputOut]
    texts: list[CopyOut]


class ScrnIn(CallInput):
    language: str  # the source language: copy is written in it
    profile: str
    vocab: list[str]  # closed vocabularies, one line each (data, rendered by code)
    facts: str  # frozen facts (identical for every call of the run)
    glossary: str  # the shared glossary (identical for every call of the run)
    slots: list[SlotIn]  # this call's screens
