"""Loaders for the derivation data files (``derive/data/*.yaml``): content lives in data, never in logic (hard rule 8)."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

DATA_DIR = Path(__file__).parent / "data"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataError(ValueError):
    """A derivation data file is malformed."""


@cache
def _raw(name: str) -> dict[str, Any]:
    path = DATA_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise DataError(f"{path.name}: a mapping with a `version` key is required")
    return data


# ---------------------------------------------------------------- facts


class FactsData(_M):
    version: int
    auth_methods: dict[str, list[str]]
    managed_accounts: list[str]
    multi_role_accounts: list[str]
    keywords: dict[str, list[str]]
    integrations: dict[str, list[str]]
    identity_sensitive: list[str]
    research_signals: dict[str, list[str]]
    field_types: dict[str, list[str]]


@cache
def facts_data() -> FactsData:
    return FactsData.model_validate(_raw("facts"))


# ---------------------------------------------------------------- inventory


class SystemMatch(_M):
    types: list[str]
    words: list[str]


class EntityClasses(_M):
    log_words: list[str]
    join_words: list[str]


class PeerData(_M):
    add_when_evidence: list[str]
    add_when_level: list[str]
    level_rank: dict[str, int]
    peer_screen_type: str


class InventoryData(_M):
    version: int
    system_match: dict[str, SystemMatch]
    system_policy: dict[str, str]
    entity_classes: EntityClasses
    negative_states: list[str]
    text_field_words: list[str]
    surface_words: dict[str, list[str]]
    role_name_suffix_words: list[str]
    peers: PeerData


@cache
def inventory_data() -> InventoryData:
    return InventoryData.model_validate(_raw("inventory"))


# ---------------------------------------------------------------- release


class ReleaseData(_M):
    version: int
    core_phase: str
    extended_phase: str
    producer_levels: list[str]
    level_rank: dict[str, int]


@cache
def release_data() -> ReleaseData:
    return ReleaseData.model_validate(_raw("release"))


# ---------------------------------------------------------------- seed


class FieldTypeMap(_M):
    field: str
    input: str | None = None
    media: bool = False


class RegionTemplate(_M):
    key: str
    shape: str
    importance: str
    what: str
    bind: str | None = None
    when: str | None = None
    attrs: list[str] = []
    cardinality: str | None = None
    item: bool = False


class IntegrationDefaults(_M):
    flow: str
    interruption: str
    pending_timeout: str
    events: list[str]
    mock_behaviour: str


class Placeholders(_M):
    purpose_with_entities: str
    purpose_plain: str
    story_so_that: str
    unhappy_path: str
    persona_goal: str


class SeedData(_M):
    version: int
    field_types: dict[str, FieldTypeMap]
    default_field_type: str
    operation_errors: dict[str, list[dict[str, str]]]
    access_levels: dict[str, list[str]]
    region_templates: dict[str, list[RegionTemplate]]
    mutation_intent: dict[str, str]
    integration_defaults: IntegrationDefaults
    assumed_behaviors: dict[str, str]
    placeholders: Placeholders
    system_effects: dict[str, list[str]]
    system_links: list[list[str]]
    home_links: list[str]
    non_home_types: list[str]
    open_types: list[str]
    web_form_inputs: int
    max_key_fields: int
    max_entities_per_screen: int


@cache
def seed_data() -> SeedData:
    return SeedData.model_validate(_raw("seed"))


# ---------------------------------------------------------------- honesty (input quality, gate notes, requested capabilities)


class HonestyData(_M):
    version: int
    weak_inputs: dict[str, Any]
    unverified: dict[str, str]
    research_stale: dict[str, str]
    payment_mismatch: dict[str, Any]
    signin: dict[str, Any]
    provider: dict[str, str]
    policy_sheet: dict[str, Any]
    structure_issues: dict[str, Any]
    capability_words: dict[str, list[str]]


@cache
def honesty_data() -> HonestyData:
    return HonestyData.model_validate(_raw("honesty"))
