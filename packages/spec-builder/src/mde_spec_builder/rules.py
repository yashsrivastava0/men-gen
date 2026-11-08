"""Baseline rules: the data behind B2 (``data/rules.yaml``), validated by pydantic models.

Wording, tables and variants live in the data file; ``baseline.py`` holds the logic. Everything
here is immutable and cached.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml

from mde_contracts.common import Frozen


class LoginAction(Frozen):
    name: str
    calls: str | None = None
    success: bool = False  # goes where the old success action went
    to_signup: bool = False  # goes to the sign-up screen when there is one
    on_success: str | None = None


class AuthScreenOverride(Frozen):
    """Fields of an auth screen replaced by a sign-in variant (``None`` keeps the pack's)."""

    recipe: str | None = None
    purpose: str | None = None
    sections: list[str] | None = None
    texts: dict[str, str] = {}
    checks: list[str] | None = None
    states: dict[str, dict[str, str]] | None = None
    actions: list[LoginAction] | None = None


class OperationTemplate(Frozen):
    id: str
    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    response_user: bool = False
    notes: str | None = None


class IntegrationTemplate(Frozen):
    id: str
    name: str
    purpose: str
    notes: str | None = None
    capability: str | None = None  # stack capability id (decides ``needs_dev_build``)


class AuthVariant(Frozen):
    label: str
    summary: str
    otp: bool = True  # False: one-time-code operations and integrations are removed
    login: AuthScreenOverride | None = None
    signup: AuthScreenOverride | None = None
    operations: list[OperationTemplate] = []
    integrations: list[IntegrationTemplate] = []
    drop_recipes: list[str] = []
    drop_auth_screens: bool = False


class PaymentsRules(Frozen):
    integration_prefix: str
    model_notes: dict[str, str]
    mock_note: str
    adapter_note: str
    real_note: str


class DataSourceText(Frozen):
    seed: str
    mock: str


class BehaviorRules(Frozen):
    offline: dict[str, str]
    notifications_on: list[str]
    notifications_off_note: str
    analytics_base: list[str]
    analytics_flow_event: str
    accessibility: dict[str, list[str]]
    error_conventions: list[str]
    permission_prompts: dict[str, str]


class ComplianceRule(Frozen):
    label: str
    impact: str
    behavior: dict[str, list[str]] = {}
    acceptance: list[str] = []
    analytics_off: bool = False


class PolicyCopyRule(Frozen):
    label: str
    impact: str
    acceptance: str
    behavior_error_convention: str | None = None


class SlotNote(Frozen):
    label: str
    impact: str


class DesignRules(Frozen):
    imagery_rules: list[str]
    component_variants: dict[str, list[str]]
    appearance_key: str
    font_key: str
    color_names: dict[str, str]
    theme_tones: dict[str, str]


class AcceptanceRules(Frozen):
    global_checks: list[str]
    signed_in: str
    mock_integrations: str


class SummaryTemplates(Frozen):
    lead: str
    users: str
    journeys: str
    look: str
    mocked: str
    nothing_mocked: str
    assumed: str
    research: str
    backlog: str
    fallback_sentence: str
    more_flows: str


class QuestionOption(Frozen):
    label: str
    description: str = ""
    sets: dict[str, str | list[str]] = {}


class QuestionTemplate(Frozen):
    slot: str
    text: str
    options: list[QuestionOption]


class ForcedRole(Frozen):
    label: str
    impact: str


class RecipeKeywords(Frozen):
    recipe: str
    words: list[str]


class ChangeMessages(Frozen):
    """Plain lines for what a change request could not do (placeholders in braces)."""

    unknown_screen: str
    no_detail: str
    secret: str
    cannot_add: str
    cannot_remove: str
    no_name: str


class ChangeOpRules(Frozen):
    removed_reason: str  # ``BacklogItem.reason`` of a screen the person removed
    default_recipe: str
    purpose: str
    recipe_keywords: list[RecipeKeywords]
    messages: ChangeMessages


class Rules(Frozen):
    version: int
    auth_methods: dict[str, AuthVariant]
    otp_operations: list[str]
    otp_integrations: list[str]
    payments: PaymentsRules
    data_source: dict[str, DataSourceText]
    behavior: BehaviorRules
    compliance_flags: dict[str, ComplianceRule]
    policy_copy: dict[str, PolicyCopyRule]
    slot_notes: dict[str, SlotNote]
    default_impact: str
    forced_role: ForcedRole
    design: DesignRules
    acceptance: AcceptanceRules
    summary: SummaryTemplates
    change_ops: ChangeOpRules
    questions: dict[str, QuestionTemplate]


@lru_cache(maxsize=1)
def load_rules() -> Rules:
    """Load and validate ``data/rules.yaml`` (cached)."""
    text = resources.files("mde_spec_builder").joinpath("data/rules.yaml").read_text("utf-8")
    raw = yaml.safe_load(text)
    acceptance = raw["acceptance"]
    raw["acceptance"] = {
        "global_checks": acceptance["global"],
        "signed_in": acceptance["signed_in"],
        "mock_integrations": acceptance["mock_integrations"],
    }
    return Rules.model_validate(raw)
