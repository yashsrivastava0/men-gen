"""Serper adapter: ``POST https://google.serper.dev/search`` over plain ``httpx`` (Google results).

Serper publishes no open API reference: the parameter list below comes from third-party wrappers and
examples, the endpoint, the ``X-API-KEY`` header, the free allowance and the pricing table from
serper.dev itself (opened 2026-11-11; see ``docs/research-agent/provider-facts.md`` for what is verified
and what is not).

How a ``SearchRequest`` maps:

* country: ``gl`` = the lowercase alpha-2 code; language: ``hl`` ("zh-cn" is sent as "zh-CN"). A value
  with the wrong shape is not sent and the result is ``locale_weak``.
* domains: ``restrict`` becomes ``site:`` operators in the query, ``(site:a.com OR site:b.com) query``
  (at most 10 domains). ``prefer`` has no native form and is NOT emulated: nothing is sent and
  ``applied`` carries no ``domain_mode``.
* exact: the query as one quoted phrase, with ``autocorrect`` off so Google does not rewrite it.
* recency: ``tbs`` is ``qdr:m`` for one month or less and ``qdr:y`` for anything longer. That is coarser
  than the request (3 months becomes 12; 24 months also becomes 12, which is narrower), and ``applied``
  reports the window that was really used ("1m" or "12m").
* want_content: ignored, Serper returns links and snippets only (``capabilities.page_content`` is False).
* max_results: ``num``, clamped to 1..100. Cost: 1 credit up to 10 results; above 10 the adapter books 2
  credits (third-party report) unless the response carries its own ``credits`` count, which wins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from mde_research_agent.adapters.search.common import (
    JsonSearchAdapter,
    clamp_results,
    clean_query,
    effective_recency_months,
    load_prices,
    normalize_country_code,
    normalize_domains,
    normalize_language,
    parse_published,
    price_int,
    quote_phrase,
    text_of,
)
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchRequest,
    SearchResult,
)

SERPER_BASE_URL = "https://google.serper.dev"
MAX_NUM = 100
MAX_SITE_OPERATORS = 10

CAPABILITIES = Capabilities(
    country=True,
    language=True,
    domain_prefer=False,  # no native prefer; restrict is emulated with site: operators
    domain_restrict=True,
    exact_match=True,  # Google's quoted phrase
    time_range=True,  # coarse: past month or past year
    page_content=False,  # links and snippets only
)


@dataclass(frozen=True)
class _Plan:
    body: dict[str, Any]
    applied: dict[str, str]
    locale_weak: bool
    num: int


def _site_clause(domains: tuple[str, ...]) -> str:
    operators = [f"site:{domain}" for domain in domains]
    return operators[0] if len(operators) == 1 else "(" + " OR ".join(operators) + ")"


class SerperAdapter(JsonSearchAdapter):
    """``SearchPort`` over Serper. Only ``SearchError`` escapes ``search``."""

    _label = "Serper"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 8.0,
        cost_per_call_micros: int | None = None,
        today: Callable[[], date] | None = None,
        base_url: str = SERPER_BASE_URL,
    ) -> None:
        super().__init__(
            api_key,
            client=client,
            timeout_s=timeout_s,
            cost_per_call_micros=cost_per_call_micros,
            today=today,
            base_url=base_url,
        )
        self.name = "serper"
        self.capabilities = CAPABILITIES

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"X-API-KEY": key}

    # -- request ---------------------------------------------------------------------------------

    def _plan(self, request: SearchRequest) -> _Plan:
        query = clean_query(request.query)
        if not query:
            raise SearchError("bad_request", "the query is empty")
        applied: dict[str, str] = {}
        locale_weak = False
        num = clamp_results(request.max_results, low=1, high=MAX_NUM)
        text = query
        if request.exact:
            text = quote_phrase(query)
            applied["exact"] = "true"
        if request.domain_mode == "restrict":
            domains = normalize_domains(request.domains, limit=MAX_SITE_OPERATORS)
            if domains:
                text = f"{_site_clause(domains)} {text}"
                applied["domain_mode"] = "restrict"
                applied["domains"] = ",".join(domains)
        body: dict[str, Any] = {"q": text, "num": num}
        if request.exact:
            body["autocorrect"] = False
        if request.country:
            country = normalize_country_code(request.country)
            if country is None:
                locale_weak = True
            else:
                body["gl"] = country
                applied["country"] = country
        if request.language:
            parsed = normalize_language(request.language)
            if parsed is None:
                locale_weak = True
            else:
                lang, region = parsed
                code = f"{lang}-{region.upper()}" if region else lang
                body["hl"] = code
                applied["language"] = code
        months = effective_recency_months(request)
        if months is not None:
            body["tbs"], applied["recency"] = ("qdr:m", "1m") if months <= 1 else ("qdr:y", "12m")
        return _Plan(body, applied, locale_weak, num)

    def _cost(self, num: int, data: dict[str, Any]) -> int:
        if self._cost_override is not None:
            return self._cost_override
        prices = load_prices()["serper"]
        credits = data.get("credits")
        if isinstance(credits, bool) or not isinstance(credits, int) or not 1 <= credits <= 10:
            heavy = num > price_int(prices, "num_threshold")
            credits = price_int(
                prices, "credits_when_num_over_10" if heavy else "credits_per_query"
            )
        return credits * price_int(prices, "micros_per_credit")

    # -- response --------------------------------------------------------------------------------

    def _organic(self, data: object) -> tuple[dict[str, Any], list[Any]]:
        if not isinstance(data, dict):
            raise SearchError(
                "bad_response", "Serper answered with something that is not an object"
            )
        organic = data.get("organic")
        if isinstance(organic, list):
            return data, organic
        if organic is None and ("searchParameters" in data or "credits" in data):
            return data, []  # a search that found nothing leaves `organic` out
        raise SearchError("bad_response", "Serper answered without an organic results list")

    def _hits(self, organic: list[Any], plan: _Plan) -> tuple[SearchHit, ...]:
        today = self._today()
        hits: list[SearchHit] = []
        for item in organic:
            if not isinstance(item, dict):
                continue
            url = text_of(item.get("link"))
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=text_of(item.get("title")),
                    snippet=text_of(item.get("snippet")),
                    published=parse_published(item.get("date"), today),
                    rank=len(hits) + 1,
                )
            )
            if len(hits) >= plan.num:
                break
        return tuple(hits)

    async def _search(self, request: SearchRequest) -> SearchResult:
        plan = self._plan(request)
        raw, latency_ms = await self._post_json("/search", plan.body)
        data, organic = self._organic(raw)
        return SearchResult(
            hits=self._hits(organic, plan),
            provider=self.name,
            cost_micros=self._cost(plan.num, data),
            applied=plan.applied,
            locale_weak=plan.locale_weak,
            latency_ms=latency_ms,
        )
