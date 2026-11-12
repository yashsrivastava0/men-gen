"""Tavily Search adapter: ``POST https://api.tavily.com/search`` over plain ``httpx``.

Facts used here were read in the official OpenAPI spec and credits page on 2026-11-11 (see
``docs/research-agent/provider-facts.md``): bearer authentication, ``country`` as a lowercase country NAME
(only for ``topic`` "general", which this adapter always sends), ``language`` with the optional strict
``filter_by_language``, ``include_domains`` with ``include_domains_mode`` "restrict" or "prefer",
``exact_match``, ``start_date``, ``include_raw_content`` and ``max_results`` 0 to 20.

How a ``SearchRequest`` maps:

* country: the code is looked up in ``countries.yaml``; an unknown code is not sent and the result is
  ``locale_weak``. Tavily boosts the country, it does not restrict to it.
* language: ISO 639-1 code, a ranking boost (``filter_by_language`` only when the adapter is built with
  ``filter_by_language=True``, because strict filtering can return few or no results). A value that is not
  an ISO 639-1 code (for example "hi-Latn") is not sent and the result is ``locale_weak``.
* domains: ``restrict`` and ``prefer`` are both native (``include_domains_mode``).
* exact: ``exact_match`` plus quotes: a quoted phrase in the query stays as written, else the whole query is quoted.
* recency: ``start_date`` = today minus N calendar months, so any N is exact; results with no detectable
  date are kept (``filter_by_published_date`` is not set).
* want_content: ``include_raw_content`` "text" and the text comes back as ``SearchHit.content``.
* max_results: clamped to 1..20.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import httpx

from mde_research_agent.adapters.search.common import (
    JsonSearchAdapter,
    clamp_results,
    clean_query,
    country_info,
    effective_recency_months,
    finite_float,
    load_prices,
    normalize_domains,
    normalize_language,
    parse_published,
    price_int,
    quote_phrase,
    subtract_months,
    text_of,
)
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchRequest,
    SearchResult,
)

TAVILY_BASE_URL = "https://api.tavily.com"
MAX_RESULTS = 20
MAX_INCLUDE_DOMAINS = 300
SearchDepth = Literal["basic", "fast", "ultra-fast", "advanced"]
_DEPTHS = frozenset({"basic", "fast", "ultra-fast", "advanced"})

CAPABILITIES = Capabilities(
    country=True,
    language=True,
    domain_prefer=True,
    domain_restrict=True,
    exact_match=True,
    time_range=True,
    page_content=True,
)


@dataclass(frozen=True)
class _Plan:
    body: dict[str, Any]
    applied: dict[str, str]
    locale_weak: bool
    max_results: int


class TavilyAdapter(JsonSearchAdapter):
    """``SearchPort`` over Tavily. Only ``SearchError`` escapes ``search``."""

    _label = "Tavily"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 8.0,
        cost_per_call_micros: int | None = None,
        today: Callable[[], date] | None = None,
        base_url: str = TAVILY_BASE_URL,
        search_depth: SearchDepth = "basic",
        filter_by_language: bool = False,
    ) -> None:
        super().__init__(
            api_key,
            client=client,
            timeout_s=timeout_s,
            cost_per_call_micros=cost_per_call_micros,
            today=today,
            base_url=base_url,
        )
        if search_depth not in _DEPTHS:
            raise ValueError(f"search_depth must be one of {sorted(_DEPTHS)}")
        self.name = "tavily"
        self.capabilities = CAPABILITIES
        self._search_depth: SearchDepth = search_depth
        self._filter_by_language = filter_by_language

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    # -- request ---------------------------------------------------------------------------------

    def _plan(self, request: SearchRequest) -> _Plan:
        query = clean_query(request.query)
        if not query:
            raise SearchError("bad_request", "the query is empty")
        applied: dict[str, str] = {}
        locale_weak = False
        max_results = clamp_results(request.max_results, low=1, high=MAX_RESULTS)
        body: dict[str, Any] = {
            "query": query,
            "search_depth": self._search_depth,
            "topic": "general",  # `country` is only valid for the general topic
            "max_results": max_results,
            "include_published_date": True,
        }
        if request.exact:
            body["query"] = quote_phrase(query)
            body["exact_match"] = True
            applied["exact"] = "true"
        if request.country:
            info = country_info(request.country)
            if info is None or info.name is None:
                locale_weak = True  # unknown, or a country Tavily's enum does not list
            else:
                body["country"] = info.name
                applied["country"] = info.name
        if request.language:
            parsed = normalize_language(request.language)
            if parsed is None:
                locale_weak = True
            else:
                code = "-".join(part for part in parsed if part)
                body["language"] = code
                applied["language"] = code
                if self._filter_by_language:
                    body["filter_by_language"] = True
        if request.domain_mode in ("prefer", "restrict"):
            domains = normalize_domains(request.domains, limit=MAX_INCLUDE_DOMAINS)
            if domains:
                body["include_domains"] = list(domains)
                body["include_domains_mode"] = request.domain_mode
                applied["domain_mode"] = request.domain_mode
                applied["domains"] = ",".join(domains)
        months = effective_recency_months(request)
        if months is not None:
            start = subtract_months(self._today(), months)
            body["start_date"] = start.isoformat()
            applied["recency"] = f"{months}m"
        if request.want_content:
            body["include_raw_content"] = "text"
        return _Plan(body, applied, locale_weak, max_results)

    def _cost(self, want_content: bool) -> int:
        if self._cost_override is not None:
            return self._cost_override
        prices = load_prices()["tavily"]
        credits = price_int(prices, "credits_per_search", self._search_depth)
        if want_content:
            credits += price_int(prices, "raw_content_extra_credits")
        return credits * price_int(prices, "micros_per_credit")

    # -- response --------------------------------------------------------------------------------

    def _hits(self, data: object, plan: _Plan, want_content: bool) -> tuple[SearchHit, ...]:
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchError("bad_response", "Tavily answered without a results list")
        today = self._today()
        hits: list[SearchHit] = []
        for item in data["results"]:
            if not isinstance(item, dict):
                continue
            url = text_of(item.get("url"))
            if not url:
                continue
            raw = item.get("raw_content") if want_content else None
            content = raw if isinstance(raw, str) and raw.strip() else None
            hits.append(
                SearchHit(
                    url=url,
                    title=text_of(item.get("title")),
                    snippet=text_of(item.get("content")),
                    content=content,
                    published=parse_published(item.get("published_date"), today),
                    rank=len(hits) + 1,
                    provider_score=finite_float(item.get("score")),
                )
            )
            if len(hits) >= plan.max_results:
                break
        return tuple(hits)

    async def _search(self, request: SearchRequest) -> SearchResult:
        plan = self._plan(request)
        data, latency_ms = await self._post_json("/search", plan.body)
        hits = self._hits(data, plan, request.want_content)
        return SearchResult(
            hits=hits,
            provider=self.name,
            cost_micros=self._cost(request.want_content),
            applied=plan.applied,
            locale_weak=plan.locale_weak,
            latency_ms=latency_ms,
        )
