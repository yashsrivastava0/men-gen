"""PROD-INTEG input and output (portable subset: flat closed records, no optional or nullable field, depth 3)."""

from __future__ import annotations

from pydantic import Field

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import ProvIn, W
from mde_spec_agent.lanes.product.kit.types import Mode


class IntegRec(W):
    id: str  # an integration id of the plan (one record per need)
    need_key: str
    name: str
    mode: Mode
    provider_side_flow: bool = False
    flow: str = ""  # provider-neutral flow
    interruption: str = (
        ""  # what happens when the external flow is interrupted (required for provider-side flows)
    )
    pending_timeout: str = (
        ""  # what happens while a result is pending, and when waiting ends (required likewise)
    )
    mock_behaviour: str = ""
    provider_ids: list[str] = Field(
        default_factory=list
    )  # dossier ids or provider names of the closed list only
    claims: list[str] = Field(default_factory=list)  # research item ids the record rests on
    prov: list[str] = Field(default_factory=list)


class EventRec(W):
    integration: str
    name: str  # a webhook or event name, e.g. succeeded, failed, pending


class IntegOut(W):
    integrations: list[IntegRec] = Field(default_factory=list)
    events: list[EventRec] = Field(default_factory=list)


class IntegIn(ProvIn):
    language: str
    profile: str
    slice: list[str]  # the integration needs of the plan, as lines (untrusted)
    facts: list[str]  # the Brief's facts that mention an integration (untrusted)
    research: list[str]  # pack(need_card, lane integrations) as lines (untrusted)
    research_note: str = ""
    integration_ids: list[str]
    provider_names: list[
        str
    ]  # the closed list, rows "id|name|need": dossiers (id, provider) and the Brief's own words (id -)
    baseline: IntegOut  # the code-made draft (not shown)
    vocab_modes: list[str] = Field(default_factory=lambda: list(vocab.integration_modes()))
