"""PROD-JOURNEYS input and output (portable subset: flat closed records in two arrays, no optional or nullable field).
A flow is a record; its stages are separate records tied to it by `flow`. A variant (first run, returning, cross app,
recovery) is a flow of its own with `of` naming the flow it varies; code mints its id."""

from __future__ import annotations

from pydantic import Field

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import ProvIn, W
from mde_spec_agent.lanes.product.kit.types import Emotion, MockProfile, Variant


class FlowRec(W):
    id: str  # a flow id of the draft, or NEW-<key> for a variant
    of: str = ""  # for a variant: the draft flow it varies
    name: str
    variant: Variant
    primary: bool = False
    role_ids: list[str] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    mock_profile: MockProfile = "typical"
    moments: list[int] = Field(default_factory=list)  # numbers of the stages that matter most
    unhappy: list[str] = Field(
        default_factory=list
    )  # the flow's own alternates first, then failure paths
    prov: list[str] = Field(default_factory=list)


class StageRec(W):
    flow: str
    n: int
    goal: str
    screen_ids: list[str] = Field(default_factory=list)
    action_keys: list[str] = Field(default_factory=list)
    emotion: Emotion = "calm"
    emotion_ref: str = ""  # the id of the fact that states the emotion; empty = assumed
    pain: str = ""


class JourneysOut(W):
    flows: list[FlowRec] = Field(default_factory=list)
    stages: list[StageRec] = Field(default_factory=list)


class ScreenActions(W):
    id: str
    app_id: str
    role_ids: list[str]
    action_keys: list[str]


class JourneysIn(ProvIn):
    language: str
    profile: str
    slice: list[str]  # the flows, their screens and actions as lines (untrusted)
    facts: list[str]  # the Brief's facts that apply, including any that state a feeling (untrusted)
    research: list[str]  # pack(need_card) lines (untrusted)
    research_note: str = ""
    flow_ids: list[str]  # the draft flows of this call (one record each)
    variants_wanted: list[
        str
    ]  # "first_run:FLW-001" rows: variants this call must also write (deep apps)
    primary_only_emotion: bool = False  # compact: only the primary journey carries an emotion line
    role_ids: list[str]
    app_ids: list[str]
    screens: list[ScreenActions]  # structured (not shown)
    baseline: JourneysOut  # the code-made draft (not shown)
    vocab_emotions: list[str] = Field(default_factory=lambda: list(vocab.emotions()))
    vocab_variants: list[str] = Field(default_factory=lambda: list(vocab.flow_variants()))
    vocab_mock_profiles: list[str] = Field(default_factory=lambda: list(vocab.mock_profile_names()))
