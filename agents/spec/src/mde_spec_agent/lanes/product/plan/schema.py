"""SPEC-PLAN input and output (portable subset: flat closed records in arrays, no optional or nullable field, depth 3).

The plan decides what code cannot: which trivial screens merge, which too-big screens split, how modules relate, who the
roles and personas are, which module owns each entity, and a release proposal (what is in, what is later and why). It is
the one call with no safe default: if it is still invalid after its re-ask the run ends partial."""

from __future__ import annotations

from pydantic import Field

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import ProvIn, W
from mde_spec_agent.lanes.product.kit.types import Actions, Reason, Status


class MergeRec(W):
    keep: str  # the screen that stays
    drop: str  # the screen folded into it (its id becomes an alias of `keep`)
    reason: str
    prov: list[str] = Field(default_factory=list)


class SplitRec(W):
    screen: str  # an over-cap screen
    part: int  # 1 = the original keeps its id; 2 and up become new screens
    name: str
    purpose: str
    region_keys: list[str] = Field(default_factory=list)  # regions that move to this part
    action_keys: list[str] = Field(default_factory=list)  # actions that move to this part
    reason: str = ""
    prov: list[str] = Field(default_factory=list)


class ModuleRec(W):
    id: str
    purpose: str
    shared: bool = False  # used by several apps and specified once
    depends_on: list[str] = Field(default_factory=list)
    prov: list[str] = Field(default_factory=list)


class RoleRec(W):
    id: str
    goal: str
    home_screen: str = ""
    prov: list[str] = Field(default_factory=list)


class AccessRec(W):
    entity: str
    role: str
    actions: list[Actions] = Field(
        default_factory=list
    )  # a row you CHANGE from the draft; usually none
    prov: list[str] = Field(default_factory=list)


class OwnerRec(W):
    entity: str
    module: str  # the ONE module that owns the entity
    reason: str = ""
    prov: list[str] = Field(default_factory=list)


class PersonaRec(W):
    role: str
    goal: str
    context: str = ""
    frequency: str = ""
    skill: str = ""
    pain_points: list[str] = Field(default_factory=list)
    success: str = ""
    prov: list[str] = Field(default_factory=list)


class ReleaseRec(W):
    feature: str
    status: Status
    reason: Reason = "none"
    why: str = ""
    consent_needed: bool = False  # a deferral of something the user asked for needs the user's yes
    prov: list[str] = Field(default_factory=list)


class PlanOut(W):
    merges: list[MergeRec] = Field(default_factory=list)
    splits: list[SplitRec] = Field(default_factory=list)
    modules: list[ModuleRec] = Field(default_factory=list)
    roles: list[RoleRec] = Field(default_factory=list)
    access: list[AccessRec] = Field(default_factory=list)
    owners: list[OwnerRec] = Field(default_factory=list)
    personas: list[PersonaRec] = Field(default_factory=list)
    release: list[ReleaseRec] = Field(default_factory=list)


class PlanIn(ProvIn):
    language: str
    profile: str
    slice_cap_tokens: int
    scope_cap: int
    brief: list[
        str
    ]  # the Compass view in summary: names, counts, roles, features, modules, entities (untrusted)
    inventory: list[str]  # the derived screen inventory, one line per screen (untrusted)
    release_lines: list[
        str
    ]  # the derived release plan: features in or later, pull-ins, consents (untrusted)
    research: list[str]  # the research digest (untrusted)
    research_note: str = ""
    module_ids: list[str]
    role_ids: list[str]
    entity_ids: list[str]
    screen_ids: list[str]
    system_screen_ids: list[str]
    over_cap_screen_ids: list[str]
    feature_ids: list[str]
    user_deferred: list[str]  # features the user deferred: they stay later, reason user
    user_must: list[str]  # user-evidenced musts: never deferred without a consent request
    screen_apps: list[str]  # "SCR-001=APP-001"
    screen_roles: list[str]  # "SCR-001=ROL-001,ROL-002"
    screen_keys: list[str]  # "SCR-021|regions=a,b|actions=c,d" for over-cap screens
    module_apps: list[str]  # "MOD-001=APP-001,APP-002"
    allow_restructure: bool = True  # False in the fused call: no merges and no splits
    baseline: PlanOut  # the code-made draft (not shown)
    vocab_actions: list[str] = Field(default_factory=lambda: list(vocab.ACCESS_ACTIONS))
    vocab_reasons: list[str] = Field(default_factory=lambda: list(vocab.release_reasons()))
