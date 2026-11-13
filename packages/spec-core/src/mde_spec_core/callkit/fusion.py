"""The fusion rule, model-independent (doc 18 section 1.2; doc 15 8.2, 8.3).

Two jobs share one call only if ALL hold for the writer profile:

1. the estimated output of the fused call is at most ``max_output_share`` x ``max_output_tokens``;
2. the call input stays under ``min(cap_share x effective window, long-prompt threshold)``;
3. the fused schema compiles within the profile's schema limits (``schema_check``).

Otherwise the call splits back into its parts. Items per call:
``clamp(floor(share x max_output_tokens / avg_item_tokens), 1, kind_cap)``. A stronger profile gets
fewer, fuller calls and a weaker one more, smaller calls, with no change to the content.

Unknown limits degrade CONSERVATIVELY and never crash (documented here, reported in ``reasons``):

* ``max_output_tokens`` unknown: nothing fuses (``can_fuse`` is False, reason ``unknown_max_output``)
  and ``items_per_call`` returns ``UNKNOWN_OUTPUT_ITEMS_PER_CALL`` (one item). Fill the profile to
  get fused, fuller calls; an honest unknown is never turned into a guessed number.
* effective window unknown: ``ConsumerProfile.effective_context()`` already falls back to a fraction
  of the nominal window (profiles.py); no extra guess is added here.
* long-prompt threshold unknown: that clause is skipped (there is no surcharge to protect against).
* a schema limit unknown: that limit is unchecked and listed in ``unchecked`` (not a failure).
* a job with no schema model and no metrics: its schema cannot be proven to compile, so the job does
  not fuse with others (a single job on its own is judged on the size clauses only).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from mde_spec_core.config import SpecConfig
from mde_spec_core.profiles import ConsumerProfile

from .schema_check import (
    SchemaMetrics,
    analyse_model,
    combine_metrics,
    compare_limits,
)

UNKNOWN_OUTPUT_ITEMS_PER_CALL = 1


@dataclass(frozen=True)
class FusionJob:
    """One unit of work that could share a call. Token numbers are estimates made by code."""

    name: str
    est_output_tokens: int
    est_input_tokens: int
    schema_model: type[BaseModel] | None = None
    metrics: SchemaMetrics | None = None  # precomputed; wins over schema_model

    def schema_metrics(self) -> SchemaMetrics | None:
        if self.metrics is not None:
            return self.metrics
        if self.schema_model is not None:
            return analyse_model(self.schema_model)[0]
        return None


@dataclass(frozen=True)
class FusionDecision:
    ok: bool
    reasons: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = ()
    est_output_tokens: int = 0
    output_cap: int | None = None
    est_input_tokens: int = 0
    input_cap: int = 0
    metrics: SchemaMetrics | None = field(default=None)


def output_cap(profile: ConsumerProfile, config: SpecConfig) -> int | None:
    if not profile.max_output_tokens:
        return None
    return math.floor(config.fusion.max_output_share * profile.max_output_tokens)


def input_cap(profile: ConsumerProfile, config: SpecConfig) -> int:
    cap = math.floor(
        config.fusion.input_cap_share_of_effective_window * profile.effective_context()
    )
    threshold = profile.price.long_prompt_threshold_tokens
    if threshold:
        cap = min(cap, threshold)
    return cap


def can_fuse(
    jobs: Sequence[FusionJob], profile: ConsumerProfile, config: SpecConfig
) -> FusionDecision:
    """May these jobs share ONE call on this writer profile? (A single job asks: does it fit?)"""
    reasons: list[str] = []
    unchecked: list[str] = []
    est_out = sum(j.est_output_tokens for j in jobs)
    est_in = sum(j.est_input_tokens for j in jobs)

    ocap = output_cap(profile, config)
    if ocap is None:
        reasons.append("unknown_max_output: the profile has no max_output_tokens; not fused")
    elif est_out > ocap:
        reasons.append(f"output: estimated {est_out} tokens exceeds {ocap} (share of max output)")

    icap = input_cap(profile, config)
    if est_in > icap:
        reasons.append(f"input: estimated {est_in} tokens exceeds {icap}")

    metrics: SchemaMetrics | None = None
    parts = [j.schema_metrics() for j in jobs]
    known = [p for p in parts if p is not None]
    if len(known) != len(parts):
        if len(jobs) > 1:
            reasons.append("schema_unknown: a job has no schema to check; not fused")
    elif known:
        metrics = combine_metrics(known)
        for issue in compare_limits(metrics, profile.schema_limits, path="fused"):
            (reasons if issue.is_error else unchecked).append(f"schema: {issue.message}")

    return FusionDecision(
        ok=not reasons,
        reasons=tuple(reasons),
        unchecked=tuple(unchecked),
        est_output_tokens=est_out,
        output_cap=ocap,
        est_input_tokens=est_in,
        input_cap=icap,
        metrics=metrics,
    )


def plan_fusion(
    jobs: Sequence[FusionJob], profile: ConsumerProfile, config: SpecConfig
) -> list[list[FusionJob]]:
    """Greedy, order-preserving grouping: add a job to the current call while the rule still holds.

    A job that does not fit even alone gets its own call (the caller then splits its items).
    """
    calls: list[list[FusionJob]] = []
    current: list[FusionJob] = []
    for job in jobs:
        if current and can_fuse([*current, job], profile, config).ok:
            current.append(job)
            continue
        if current:
            calls.append(current)
        current = [job]
    if current:
        calls.append(current)
    return calls


def items_per_call(
    profile: ConsumerProfile, avg_item_tokens: float, kind_cap: int, config: SpecConfig
) -> int:
    """``clamp(floor(share x max_output / avg item tokens), 1, kind_cap)``; unknown output: 1."""
    cap = max(1, kind_cap)
    ocap = output_cap(profile, config)
    if ocap is None:
        return min(UNKNOWN_OUTPUT_ITEMS_PER_CALL, cap)
    fit = math.floor(ocap / max(avg_item_tokens, 1.0))
    return max(1, min(fit, cap))


def calls_needed(
    n_items: int,
    profile: ConsumerProfile,
    avg_item_tokens: float,
    kind_cap: int,
    config: SpecConfig,
) -> int:
    """How many calls ``n_items`` of one kind take on this profile."""
    if n_items <= 0:
        return 0
    return math.ceil(n_items / items_per_call(profile, avg_item_tokens, kind_cap, config))


def unfuse_needed(first_try_pass_rate: float, profile_name: str, config: SpecConfig) -> bool:
    """True if a call kind's first-try pass rate is below the detail profile's bar (PS-5).

    ``profile_name`` is the detail profile (compact, standard, deep). An unknown name uses the
    strictest bar (conservative: more likely to un-fuse).
    """
    bars = config.fusion.unfuse_first_try_pass_below
    bar = bars.get(profile_name)
    if bar is None:
        bar = max(bars.values()) if bars else 1.0
    return first_try_pass_rate < bar
