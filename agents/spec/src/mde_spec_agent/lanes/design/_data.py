"""Loader of the design lane's data file (`data/design_data.yaml`): content lives in data, never in logic (hard rule 8)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

DATA_PATH = Path(__file__).parent / "data" / "design_data.yaml"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MinMax(_M):
    min: int
    max: int


class Implications(_M):
    asset_kind: dict[str, str]
    ambition: dict[str, list[str]]
    depth: dict[str, str]
    dark_stance: dict[str, str]


class MoodCopy(_M):
    name: str
    personality: str
    voice_line: str
    goals: list[str]
    avoid: list[str]


class AdminVariant(_M):
    default_density: str
    default_motion: str
    default_ambition: str
    note: str


class AssetTemplate(_M):
    kind: str
    purpose: str
    subject: str
    avoid: list[str]


class DesignData(_M):
    version: int
    brand_colour_pattern: str
    audience_class_by_surface: dict[str, str]
    delight_budget: MinMax
    deep_dark_stances: list[str]
    deep_default_dark_stance: str
    default_dark_stance: str
    dark_stance_synonyms: dict[str, str]
    max_asset_count: int
    model_asset_kinds: list[str]
    model_asset_purposes: list[str]
    max_model_assets: dict[str, int]
    max_avoid_items: int
    capability_implications: Implications
    ordinals: list[str]
    stages: list[str]
    moods: dict[str, MoodCopy]
    admin_variant: AdminVariant
    asset_templates: dict[str, AssetTemplate]
    asset_template_order: dict[str, list[str]]
    empty_state_entities_max: dict[str, int]


@cache
def design_data() -> DesignData:
    return DesignData.model_validate(yaml.safe_load(DATA_PATH.read_text(encoding="utf-8")))
