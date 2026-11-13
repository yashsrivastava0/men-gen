"""spec-config.yaml loader. REFUSES an inconsistent budget set (doc 15 5.3)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DATA_DIR = Path(__file__).parent / "data"


class ConfigError(ValueError):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budgets(_M):
    index_cap: int
    common_global_cap: int
    task_shards_cap: int
    module_cap: int
    card_target: int
    card_hard: int
    slice_cap: int
    front_end_allowance: int
    task_read_budget: int
    task_card_cap: int
    global_shard_cap: int
    contract_resource_cap: int
    build_plan_cap: int
    glossary_max: int
    delight_block_max: int
    one_file_min_consumer_window: int
    token_factor_chars_per_token: float

    @property
    def prefix_cap(self) -> int:
        return self.index_cap + self.common_global_cap

    @property
    def whole_pack_tokens(self) -> int:
        """A whole-pack read is one task: budget minus the front end's allowance minus the task card."""
        return self.task_read_budget - self.front_end_allowance - self.task_card_cap

    @model_validator(mode="after")
    def _consistent(self) -> Budgets:
        total = (
            self.prefix_cap
            + self.task_shards_cap
            + self.module_cap
            + self.slice_cap
            + self.front_end_allowance
        )
        if total != self.task_read_budget:
            raise ConfigError(
                f"inconsistent budgets: prefix {self.prefix_cap} + shards {self.task_shards_cap} + "
                f"module {self.module_cap} + slice {self.slice_cap} + allowance "
                f"{self.front_end_allowance} = {total}, but task_read_budget is "
                f"{self.task_read_budget}; a one-screen task must always fit"
            )
        if self.card_hard > self.slice_cap or self.card_target > self.card_hard:
            raise ConfigError("card caps must satisfy card_target <= card_hard <= slice_cap")
        if self.whole_pack_tokens <= 0:
            raise ConfigError("whole_pack_tokens must be positive")
        return self


class Scope(_M):
    scope_cap_per_app_phase: int
    lookups_before_raise: int


class Profiles(_M):
    tier_floor: dict[str, str]
    raise_to_standard_if: dict[str, Any]
    raise_to_deep_if: dict[str, Any]
    acceptance_scenarios_min: dict[str, int]
    could_cap_per_app: dict[str, int]
    could_cap_per_screen: int
    directions: dict[str, int]
    hero_per_primary_entity: dict[str, list[int]]
    hero_per_other_entity: list[int]
    mock_profiles: dict[str, list[str]]
    pilot_screens_per_app: dict[str, int]
    verify_profiles: list[str]


class Fusion(_M):
    max_output_share: float
    input_cap_share_of_effective_window: float
    unfuse_first_try_pass_below: dict[str, float]
    cards_per_call_max: int
    strings_per_locale_call: int
    entities_per_call_max: int
    flows_per_journey_call: int
    integrations_per_call: int


class MobileFit(_M):
    max_primary_regions: int
    max_regions: int
    max_form_inputs: int
    max_phone_columns: int
    long_text_chars: int
    gross_breach_factor: float


class Checks(_M):
    weak_route_banner_share: float
    v45_downgrade_enabled: bool
    v45_false_positive_target: float
    mobile_fit: MobileFit
    freedom_lint_warnings_per_card_max: int
    widget_key_suffixes: list[str]
    wcag_body_text_ratio: float
    min_seed_hue_gap_degrees: float
    min_direction_axis_differences: int
    locale_script_share_min: float
    digit_run_deny_length: int


class Repair(_M):
    max_reasks_per_node: int
    verify_max_llm_rounds: int
    class_a_max_passes: int


class Verify(_M):
    profiles: list[str]
    canary_share: dict[str, float]
    max_llm_rounds: int
    canary_blocking_questions_per_screen_max: float


class Money(_M):
    cap_micro_usd: int
    tripwire_micro_usd: int
    estimate_drift_alert: float
    price_check_abort_factor: float

    @model_validator(mode="after")
    def _tripwire_below_cap(self) -> Money:
        if not 0 < self.tripwire_micro_usd < self.cap_micro_usd:
            raise ConfigError("tripwire must be positive and below the cap")
        return self


class Concurrency(_M):
    initial: int
    min: int
    additive_increase: int
    multiplicative_decrease: float
    fair_queue_weights: dict[str, int]
    quota_runs_per_day: int
    quota_tokens_per_day: int


class Proof(_M):
    blocking_questions_per_screen_max: float
    assumptions_per_screen_max: float
    acceptance_satisfiable_min: float
    first_try_pass_min: float
    repair_rate_max: float
    degraded_max: float
    planted_catch_min: float
    planted_catch_per_kind_min: float
    false_positive_max: float
    freedom_probe_distinct_layout_min: float
    lookup_recall_at_3_min: float
    wrong_gap_max: float
    estimate_error_max: float
    mobile_fit_hit_rate_max: float


class SpecConfig(_M):
    version: int
    budgets: Budgets
    scope: Scope
    profiles: Profiles
    fusion: Fusion
    checks: Checks
    repair: Repair
    verify: Verify
    money: Money
    concurrency: Concurrency
    proof: Proof


def load_config(path: Path | None = None) -> SpecConfig:
    p = path or (DATA_DIR / "spec-config.yaml")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    try:
        return SpecConfig.model_validate(raw)
    except ConfigError:
        raise
    except Exception as exc:  # pydantic wraps validator errors; keep the plain message
        if "inconsistent budgets" in str(exc):
            raise ConfigError(str(exc)) from exc
        raise


@lru_cache(maxsize=1)
def default_config() -> SpecConfig:
    return load_config()
