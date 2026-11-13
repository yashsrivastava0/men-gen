"""Consumer and writer profiles (doc 15 11.4): data, never constants in logic.

A profile describes a model ROUTE's limits and a consumer's reading limits. Values come from each
provider's current documentation with `source_url` and `checked_on`; a value nobody verified stays
`None` and is reported as unset. No model or vendor name appears in code: ids are labels in data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from .config import DATA_DIR


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SchemaLimits(_M):
    max_nullable_fields: int | None = None
    max_depth: int | None = None
    max_enum_size: int | None = None
    max_union_params: int | None = None
    max_grammar_chars: int | None = None


class ProfilePrice(_M):
    input_usd_per_million: str | None = None
    output_usd_per_million: str | None = None
    cached_input_usd_per_million: str | None = None
    long_prompt_threshold_tokens: int | None = None
    long_prompt_multiplier: str | None = None
    reasoning_billed_as_output: bool | None = None


class Limits(_M):
    requests_per_minute: int | None = None
    input_tokens_per_minute: int | None = None
    output_tokens_per_minute: int | None = None
    max_parallel: int | None = None


class ConsumerProfile(_M):
    id: str
    capability_class: Literal["weak", "standard", "strong"]
    nominal_context_tokens: int | None = None
    effective_context_tokens: int | None = None
    max_output_tokens: int | None = None
    output_tokens_per_second: float | None = None
    tokenizer_chars_per_token: float | None = None  # a conservative factor until a counter exists
    caching: Literal["explicit_breakpoints", "automatic_prefix", "cached_object", "none"] = "none"
    cache_share: float = 0.0
    structured_output: Literal["schema", "json_mode", "none"] = "schema"
    schema_limits: SchemaLimits = SchemaLimits()
    limits: Limits = Limits()
    price: ProfilePrice = ProfilePrice()
    source_url: str = ""
    checked_on: str = ""
    notes: str = ""
    unset_fields: list[str] = []  # fields nobody verified, said out loud

    def effective_context(self) -> int:
        """Budgets use the effective window; fall back to a fraction of nominal [J]."""
        if self.effective_context_tokens:
            return self.effective_context_tokens
        if self.nominal_context_tokens:
            return int(self.nominal_context_tokens * 0.5)
        return 32_000


class ProfileSet(_M):
    version: int
    profiles: list[ConsumerProfile]

    def get(self, profile_id: str) -> ConsumerProfile:
        for p in self.profiles:
            if p.id == profile_id:
                return p
        raise KeyError(profile_id)

    def weakest(self) -> ConsumerProfile:
        order = {"weak": 0, "standard": 1, "strong": 2}
        return sorted(self.profiles, key=lambda p: (order[p.capability_class], p.id))[0]


def load_profiles(path: Path | None = None) -> ProfileSet:
    p = path or (DATA_DIR / "consumer-profiles.yaml")
    return ProfileSet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
