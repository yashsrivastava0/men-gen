"""SearchPort: the one seam between the engine and a web-search provider (Tavily, Serper, ...).

The engine only knows these types. An adapter turns a ``SearchRequest`` into one provider HTTP call
(plain ``httpx``, no vendor SDK), applies what the provider supports (country, language, domain
preference or restriction, exact match, time range), says which of those it really applied
(``SearchResult.applied``; ``locale_weak`` when the place or language could not be applied), reports the
cost of the call, and maps every failure to ``SearchError``. It never raises anything else and never
logs a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol, runtime_checkable

DomainMode = Literal["none", "prefer", "restrict"]
SearchErrorKind = Literal[
    "timeout",
    "rate_limited",
    "quota",
    "auth",
    "bad_request",
    "http_error",
    "network",
    "bad_response",
]


@dataclass(frozen=True)
class SearchRequest:
    """One search. ``country`` is a lowercase ISO 3166-1 alpha-2 code ("in"), ``language`` an ISO 639-1 code."""

    query: str
    country: str | None = None
    language: str | None = None
    domain_mode: DomainMode = "none"
    domains: tuple[str, ...] = ()
    exact: bool = False  # the phrase must appear as written (emulated with quotes when unsupported)
    recency_months: int | None = None  # only results from the last N months (None = any time)
    want_content: bool = False  # ask for page text in the same call when the provider can
    max_results: int = 10


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    content: str | None = None  # page text when the provider returned it (Tavily raw content)
    published: date | None = None
    rank: int = 0  # 1-based position in the provider's own order
    provider_score: float | None = None


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    provider: str
    cost_micros: int = 0  # integer micro-dollars; 0 when free or unknown
    applied: dict[str, str] = field(
        default_factory=dict
    )  # e.g. {"country": "india", "language": "hi"}
    locale_weak: bool = False  # a requested country or language could not be applied
    latency_ms: int = 0
    cached: bool = False


class SearchError(Exception):
    """A provider call failed. ``kind`` is the reason; ``retry_after`` (seconds) comes from a 429."""

    def __init__(
        self,
        kind: SearchErrorKind,
        message: str = "",
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind: SearchErrorKind = kind
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class Capabilities:
    """What an adapter can apply natively; the rest is emulated or reported as not applied."""

    country: bool = False
    language: bool = False
    domain_prefer: bool = False
    domain_restrict: bool = False
    exact_match: bool = False
    time_range: bool = False
    page_content: bool = False  # can return page text in the same call


@runtime_checkable
class SearchPort(Protocol):
    name: str
    capabilities: Capabilities

    async def search(self, request: SearchRequest) -> SearchResult: ...
