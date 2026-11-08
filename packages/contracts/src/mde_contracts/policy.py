"""Policy profiles (spec section 10): configuration, never code paths."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_contracts.common import Depth, Frozen, Mode
from mde_contracts.slots import SlotLevel

HeavyChange = Literal["scope", "auth", "pay", "integrations", "design", "data", "roles", "screens"]


class WebPolicy(Frozen):
    allow_company_name_in_queries: bool = True
    allowed_domains: list[str] = []
    blocked_domains: list[str] = []


class ResearchPolicy(Frozen):
    enabled: bool = True
    depth: Depth = "light"
    web: WebPolicy = Field(default_factory=WebPolicy)
    connectors: list[str] = []  # internal source connectors (R6); interface only in v1


class GateRule(Frozen):
    approvers: list[str]  # required approver ROLES; all must approve the same hash
    full_file_view: bool = False
    reminder_hours: int | None = None


class PostHandoffPolicy(Frozen):
    heavy_if: list[HeavyChange] = ["scope", "auth", "pay", "integrations"]
    max_new_screens_light: int = 5


class PolicyGates(Frozen):
    brief: GateRule = Field(default_factory=lambda: GateRule(approvers=["requester"]))
    plan: GateRule = Field(default_factory=lambda: GateRule(approvers=["requester"]))
    post_handoff: PostHandoffPolicy = Field(default_factory=PostHandoffPolicy)


class Limits(Frozen):
    max_screens: int = 50
    max_cards: int = 10
    max_turns: int = 30
    session_budget_usd: str = "0.50"  # Decimal as string
    research_budget_usd: str = "0.30"
    max_new_screens_light: int = 5
    max_spec_tokens: int = 60000  # size budget of APP_SPEC.md (estimated tokens)


class ModelPolicy(Frozen):
    providers: list[str] = ["openai"]
    retention: str = "none"


class Policy(Frozen):
    id: str  # "guided-default"
    org: str = "platform"
    description: str = ""
    mode: Mode = "guided"
    inherits: str | None = None  # "platform-default@1" (max three levels)
    slot_policy: dict[str, SlotLevel] = Field(default_factory=dict)  # overrides per slot id
    research: ResearchPolicy = Field(default_factory=ResearchPolicy)
    gates: PolicyGates = Field(default_factory=PolicyGates)
    limits: Limits = Field(default_factory=Limits)
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    banned_libraries: list[str] = []
    allowed_regions: list[str] = []
    role_precedence: list[str] = []  # resolves Enterprise slot conflicts between roles
