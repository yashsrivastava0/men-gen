"""Every Research call spec in one place (for the gateway registry, docs and tests)."""

from __future__ import annotations

from typing import Any

from mde_research_agent.calls import assemble, check, gaps, plan, queries
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.calls.extract import SPECS as EXTRACT_SPECS


def all_specs() -> list[ResearchCallSpec[Any, Any]]:
    return [
        plan.SPEC,
        queries.SPEC,
        gaps.SPEC,
        *EXTRACT_SPECS.values(),
        assemble.SPEC,
        check.SPEC,
    ]
