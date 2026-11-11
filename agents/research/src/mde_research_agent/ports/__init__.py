"""Ports: the seams between the engine and the outside world (search, fetch, store, events)."""

from mde_research_agent.ports.fetch import FetchedPage, FetchPort, SkipReason
from mde_research_agent.ports.search import (
    Capabilities,
    DomainMode,
    SearchError,
    SearchErrorKind,
    SearchHit,
    SearchPort,
    SearchRequest,
    SearchResult,
)

__all__ = [
    "Capabilities",
    "DomainMode",
    "FetchPort",
    "FetchedPage",
    "SearchError",
    "SearchErrorKind",
    "SearchHit",
    "SearchPort",
    "SearchRequest",
    "SearchResult",
    "SkipReason",
]
