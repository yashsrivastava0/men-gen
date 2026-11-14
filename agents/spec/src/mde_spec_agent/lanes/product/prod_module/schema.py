"""PROD-MODULE input and output. The output is the PORTABLE SUBSET: flat closed records in arrays (an entity, its fields,
its states and transitions, rules, custom operations with their fields and errors, stories with their acceptance are
separate arrays tied together by id), no optional or nullable field, small enums, depth 3. A new node is written with an
id that starts with `NEW-`; code mints the real id. Unknown stays an empty string, 0 or an empty list."""

from __future__ import annotations

from pydantic import Field

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import ProvIn, W
from mde_spec_agent.lanes.product.kit.types import (
    EntityClass,
    ErrorCode,
    FieldType,
    Method,
    Recovery,
    Side,
    Voice,
)


class EntityRec(W):
    id: str  # an entity id of the draft, or NEW-<name> for a new join or log entity
    name: str
    klass: EntityClass
    typical_count: int = 0  # 0 = not stated
    max_count: int = 0
    prov: list[str] = Field(default_factory=list)


class FieldRec(W):
    entity: str
    key: str
    type: FieldType
    required: bool = False
    enum_values: list[str] = Field(default_factory=list)
    ref_entity: str = ""
    derived_rule: str = ""  # a rule id (RUL-..) or NEW-<key> of a rule below; never an expression
    constraint: str = ""
    media: bool = False


class StateRec(W):
    entity: str
    state: str
    initial: bool = False


class TransitionRec(W):
    entity: str
    from_state: str
    to_state: str
    roles: list[str] = Field(default_factory=list)
    on: str = ""  # an operation of this answer (NEW-<key>) or an event name, or empty


class RuleRec(W):
    id: str  # RUL-.. of the draft, or NEW-<key>
    subject: str
    statement: str
    condition: str = ""
    value: str = ""  # only when a quote states the number
    unit: str = ""
    entity: str = ""
    confirm: bool = False
    prov: list[str] = Field(default_factory=list)


class OperationRec(W):
    id: str  # NEW-<key>: a custom operation (CRUD is made by code)
    name: str
    entity: str = ""
    method: Method
    path: str
    roles: list[str] = Field(default_factory=list)
    idempotent: bool = False
    paged: bool = False
    prov: list[str] = Field(default_factory=list)


class OpFieldRec(W):
    op: str
    side: Side
    key: str
    type: FieldType
    required: bool = False


class OpErrorRec(W):
    op: str
    code: ErrorCode
    recovery: Recovery


class StoryRec(W):
    id: str  # US-.. of the draft, or NEW-<key>
    role: str
    want: str
    so_that: str
    feature_ids: list[str] = Field(default_factory=list)
    policy_id: str = ""
    screen_ids: list[str] = Field(default_factory=list)
    voice: Voice = "must"
    prov: list[str] = Field(default_factory=list)


class AcceptRec(W):
    story: str
    given: str
    when: str
    then: str


class ModuleOut(W):
    entities: list[EntityRec] = Field(default_factory=list)
    fields: list[FieldRec] = Field(default_factory=list)
    states: list[StateRec] = Field(default_factory=list)
    transitions: list[TransitionRec] = Field(default_factory=list)
    rules: list[RuleRec] = Field(default_factory=list)
    operations: list[OperationRec] = Field(default_factory=list)
    op_fields: list[OpFieldRec] = Field(default_factory=list)
    op_errors: list[OpErrorRec] = Field(default_factory=list)
    stories: list[StoryRec] = Field(default_factory=list)
    acceptance: list[AcceptRec] = Field(default_factory=list)


class ScreenRef(W):
    id: str
    name: str
    system: bool
    role_ids: list[str]
    feature_ids: list[str]
    entity_ids: list[str]


class ModuleIn(ProvIn):
    language: str
    profile: str
    module_id: str
    module_name: str
    app_ids: list[str]
    slice: list[str]  # the module's Compass slice as lines (untrusted)
    facts: list[str]  # the Brief's facts that apply, "FCT-018: ..." (untrusted)
    research: list[str]  # pack(need_card) as lines (untrusted)
    research_note: str = ""  # said when Research is thin or has nothing for this module
    feature_ids: list[str]
    in_feature_ids: list[str]
    module_feature_ids: list[str] = Field(
        default_factory=list
    )  # the in features this module serves
    feature_names: list[str] = Field(
        default_factory=list
    )  # "FTR-001=Weekly class timetable" for the in features
    role_ids: list[str]
    role_names: list[str]  # "ROL-002=Members"
    role_features: list[
        str
    ]  # "ROL-002=FTR-001,FTR-002": the in features the role's stories already serve
    screen_ids: list[str]
    entity_ids: list[str]
    rule_ids: list[str]
    story_ids: list[str]
    cover_feature_ids: list[str]  # features the stories of this call must still cover
    cover_screen_ids: list[str]  # screens the stories of this call must still cite
    screens: list[ScreenRef]  # structured (not shown): for the mock, the fallback and the checks
    baseline: ModuleOut  # the code-made draft (not shown): the mock and the fallback start from it
    vocab_field_types: list[str] = Field(default_factory=lambda: list(vocab.field_types()))
    vocab_error_codes: list[str] = Field(default_factory=lambda: list(vocab.error_codes()))
    vocab_recoveries: list[str] = Field(default_factory=lambda: list(vocab.recoveries()))
    vocab_entity_classes: list[str] = Field(default_factory=lambda: list(vocab.entity_classes()))
    vocab_voices: list[str] = Field(default_factory=lambda: list(vocab.voices()))
