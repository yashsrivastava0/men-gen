"""Research engine configuration (starting values from spec 4.2, tuned from the L0 smoke numbers)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from mde_contracts.common import Depth
from mde_core.settings import Settings

DEPTH_CAPS: Mapping[Depth, int] = {"light": 6, "standard": 12, "deep": 20}


@dataclass(frozen=True)
class ResearchConfig:
    """All tunables in one place. Money is integer micro-dollars.

    The cost figures are planning estimates used only to trim the plan to the remaining budget
    (the gateway still enforces the real caps): one ``web_search`` call measured at about
    USD 0.011 in the L0 smoke (about 8,700 input tokens of search content, 4 to 5 seconds).
    """

    depth_caps: Mapping[Depth, int] = field(default_factory=lambda: dict(DEPTH_CAPS))
    gap_fill_max_queries: int = 4  # Deep only, on top of the depth cap
    concurrency: int = 1  # RESEARCH_CONCURRENCY; results stay ordered at any value
    max_suggestions: int = 12
    max_facts: int = 60
    max_facts_per_result: int = 6
    max_sources_per_result: int = 8
    max_result_chars: int = 12_000  # of cleaned search text handed to C7
    statement_max_chars: int = 240
    query_max_chars: int = 120
    search_cost_micros: int = 12_000
    extract_cost_micros: int = 1_000
    plan_cost_micros: int = 1_500
    synthesis_cost_micros: int = 4_000
    run_cap_micros: int | None = None  # hard ceiling on top of policy.limits.research_budget_usd
    stale_after_days: int = 14

    def depth_cap(self, depth: Depth) -> int:
        return self.depth_caps[depth]

    @property
    def per_query_cost_micros(self) -> int:
        return self.search_cost_micros + self.extract_cost_micros

    @property
    def overhead_cost_micros(self) -> int:
        return self.plan_cost_micros + self.synthesis_cost_micros

    @classmethod
    def from_settings(cls, settings: Settings) -> ResearchConfig:
        return cls(concurrency=max(1, settings.research_concurrency))
