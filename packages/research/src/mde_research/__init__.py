"""Research engine: plan, search, extract, synthesize, safety (spec 4, brief 8.3).

Entry point: ``DefaultResearchEngine(gateway).run(request, run_context) -> ResearchNote``.
"""

from __future__ import annotations

from mde_research.config import DEPTH_CAPS, ResearchConfig
from mde_research.engine import (
    FALLBACKS,
    DefaultResearchEngine,
    InjectionEvent,
    PlannedQuery,
    ResearchReport,
    ResearchSpecs,
)
from mde_research.internal import NullInternalSource
from mde_research.staleness import STALE_AFTER_DAYS, is_stale

__version__ = "0.1.0"

__all__ = [
    "DEPTH_CAPS",
    "FALLBACKS",
    "STALE_AFTER_DAYS",
    "DefaultResearchEngine",
    "InjectionEvent",
    "NullInternalSource",
    "PlannedQuery",
    "ResearchConfig",
    "ResearchReport",
    "ResearchSpecs",
    "__version__",
    "is_stale",
]
