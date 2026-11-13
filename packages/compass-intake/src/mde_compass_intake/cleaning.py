"""The words of ``data/cleaning.yaml`` as one typed object (hard rule 8: content lives in data files)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

CLEANING_FILE = Path(__file__).resolve().parent / "data" / "cleaning.yaml"


class Cleaning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_provenance_types: tuple[str, ...]
    min_confidence: float
    priorities: tuple[str, ...]
    scope_statuses: tuple[str, ...]
    tiers: tuple[str, ...]
    junk_role_names: tuple[str, ...]
    junk_app_names: tuple[str, ...]
    app_name_words: tuple[str, ...]
    derived_role_prefix: str
    default_role_name: str
    admin_role_name: str
    admin_cues: tuple[str, ...]
    rule_fact_kinds: tuple[str, ...]
    mostly_nice_min_features: int
    mostly_nice_share: float
    junk_open_question_tokens: tuple[str, ...]


@lru_cache(maxsize=1)
def load_cleaning() -> Cleaning:
    return Cleaning(**(yaml.safe_load(CLEANING_FILE.read_text(encoding="utf-8")) or {}))
