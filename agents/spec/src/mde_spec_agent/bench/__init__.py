"""The Spec Bench's input side: discover Compass exports and Research runs, and propose pairs (doc 19 4.3)."""

from mde_spec_agent.bench.autoselect import Proposal, auto_select, best_research, matches
from mde_spec_agent.bench.inputs import (
    CompassEntry,
    ResearchEntry,
    SkippedFile,
    discover_compass,
    discover_compass_report,
    discover_research,
)

__all__ = [
    "CompassEntry",
    "Proposal",
    "ResearchEntry",
    "SkippedFile",
    "auto_select",
    "best_research",
    "discover_compass",
    "discover_compass_report",
    "discover_research",
    "matches",
]
