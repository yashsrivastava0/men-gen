"""All Compass tunables in one place (the only module with numbers that logic depends on).

Nothing here is secret. Money is integer micro-dollars (1 USD = 1_000_000) or ``Decimal``, never float.
Environment overrides are read in one function (``load_budget_policy``) so tests can pass a dict.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

MODEL_ID = "gpt-6-luna"
"""The one model Compass uses (through the gateway). Every call spec carries a ``lane`` so a second
vendor can be added later without redesign."""

MICROS_PER_USD = 1_000_000

Effort = Literal["none", "low", "medium", "high"]
Lane = Literal["writer", "critic", "canary"]
Route = Literal["fast", "standard", "deep"]


@dataclass(frozen=True)
class CallProfile:
    """Starting values for one structured call (prompt section 7). Reasoning tokens count against
    ``max_output_tokens``, so the caps are generous; measure in the live smoke and tune."""

    call_id: str
    effort: Effort
    max_output_tokens: int
    lane: Lane
    route: Route
    timeout_s: float


def _p(
    call_id: str, effort: Effort, cap: int, lane: Lane, route: Route, timeout: float
) -> CallProfile:
    return CallProfile(call_id, effort, cap, lane, route, timeout)


CALL_PROFILES: dict[str, CallProfile] = {
    p.call_id: p
    for p in (
        _p("cmp.read_chunk", "high", 4000, "writer", "deep", 90),
        _p("cmp.merge_claims", "high", 6000, "writer", "deep", 120),
        _p("cmp.situate", "high", 6000, "writer", "deep", 90),
        _p("cmp.understand_turn", "medium", 2500, "writer", "standard", 45),
        _p("cmp.answer", "medium", 1500, "writer", "standard", 45),
        _p("cmp.write_card", "high", 2500, "writer", "deep", 60),
        _p("cmp.scan_ambiguity", "high", 3000, "critic", "deep", 60),
        _p("cmp.seed_research", "high", 4000, "writer", "deep", 90),
        _p("cmp.probe_handoff", "low", 2500, "canary", "fast", 30),
        _p("cmp.compile_text", "high", 3500, "writer", "deep", 90),
        _p("cmp.interpret_change", "high", 3500, "writer", "deep", 90),
        _p("cmp.make_variants", "high", 2500, "writer", "deep", 60),
    )
}


@dataclass(frozen=True)
class QuestionPolicy:
    """Question budgets and the weights of the value-of-information score (pure function, tested)."""

    express_max: int = 5
    standard_target: int = 7
    hard_cap: int = 10
    large_per_module: int = 6
    # score = w_impact*impact + w_risk*risk - w_infer*inferability - w_effort*effort
    w_impact: float = 3.0
    w_risk: float = 2.0
    w_inferability: float = 2.5
    w_effort: float = 0.5
    high_impact_at: int = 4  # catalog impact 1..5
    high_inferability_at: float = 0.75
    max_followups_per_answer: int = 1
    max_question_words: int = 20
    max_option_words: int = 8
    option_count: int = 3
    card_validation_retries: int = 2  # two validation failures in a row -> fallback card


@dataclass(frozen=True)
class IntakeLimits:
    max_file_bytes: int = 15 * 1024 * 1024
    max_files: int = 10
    max_session_bytes: int = 60 * 1024 * 1024
    max_pages: int = 300
    max_urls: int = 5
    pasted_text_is_document_over: int = 3000  # characters
    url_timeout_s: float = 10.0
    url_max_bytes: int = 2 * 1024 * 1024
    url_max_redirects: int = 3
    chunk_target_tokens: tuple[int, int] = (3000, 4000)
    extract_concurrency: int = 4


@dataclass(frozen=True)
class GatePolicy:
    probe_repair_rounds: int = 2
    typed_yes_min_confidence: float = 0.8
    required_approver_roles: tuple[str, ...] = ("approver",)
    history_slice_turns: int = 6
    max_back_edge_loops: int = 2


@dataclass(frozen=True)
class BudgetPolicy:
    """The Compass-only live-test cap, additive to the repo's global cap and ledger (prompt 4)."""

    hard_cap_usd: Decimal = Decimal("2.00")
    cap_usd: Decimal = Decimal("2.00")  # ``COMPASS_LIVE_BUDGET_USD`` clamped to <= hard_cap_usd
    stop_fraction: Decimal = Decimal("0.95")  # effective stop at 95% of the cap (1.90)
    per_session_cap_usd: Decimal = Decimal("0.25")
    reserve_usd: Decimal = Decimal("0.50")  # kept unspent across the live batches

    @property
    def effective_stop_micros(self) -> int:
        return int(self.cap_usd * self.stop_fraction * MICROS_PER_USD)

    @property
    def per_session_micros(self) -> int:
        return int(self.per_session_cap_usd * MICROS_PER_USD)

    @property
    def cap_micros(self) -> int:
        return int(self.cap_usd * MICROS_PER_USD)


def load_budget_policy(env: Mapping[str, str] | None = None) -> BudgetPolicy:
    """``COMPASS_LIVE_BUDGET_USD`` is clamped: unparsable, non-positive or above 2.00 never widens it."""
    source = os.environ if env is None else env
    hard = BudgetPolicy().hard_cap_usd
    raw = source.get("COMPASS_LIVE_BUDGET_USD")
    cap = hard
    if raw is not None:
        try:
            parsed = Decimal(raw.strip())
        except Exception:
            parsed = hard
        if parsed.is_finite() and parsed > 0:
            cap = min(parsed, hard)
    return BudgetPolicy(cap_usd=cap)


@dataclass(frozen=True)
class CompassConfig:
    questions: QuestionPolicy = field(default_factory=QuestionPolicy)
    intake: IntakeLimits = field(default_factory=IntakeLimits)
    gate: GatePolicy = field(default_factory=GatePolicy)
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    default_language: str = "en"
    supported_languages: tuple[str, ...] = ("en", "hi")
    max_event_text_chars: int = 20_000
    sse_poll_seconds: float = 0.25
    sse_max_seconds: float = 900.0
    prefetch_enabled: bool = False  # stretch goal, default off


DEFAULT_CONFIG = CompassConfig()
