"""TavilyAdapter against hand-written bodies modelled on the official Tavily Search reference.

No network: every call goes through ``httpx.MockTransport``. Nothing here reads the environment.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_research_agent.adapters.search import common
from mde_research_agent.adapters.search.tavily import TavilyAdapter
from mde_research_agent.ports.search import SearchError, SearchPort, SearchRequest, SearchResult

TODAY = date(2026, 11, 11)
KEY = "tvly-NOT-A-REAL-KEY-0001"  # secret-scan: allow

ALLOWED_BODY_KEYS = frozenset(
    {
        "query",
        "search_depth",
        "topic",
        "max_results",
        "include_published_date",
        "exact_match",
        "country",
        "language",
        "filter_by_language",
        "include_domains",
        "include_domains_mode",
        "start_date",
        "include_raw_content",
    }
)  # every parameter the adapter may send; all are in the official OpenAPI spec (2026-11-11)

SAMPLE_RESULTS: list[dict[str, Any]] = [
    {
        "url": "https://www.nashik.gov.in/produce-rules",
        "title": "Produce market rules",
        "content": "Snippet one [...] more of it",
        "score": 0.91,
        "raw_content": "FULL PAGE TEXT ONE",
        "published_date": "2025-03-12",
    },
    {
        "url": "https://b.example.com/guide",
        "title": "A guide",
        "content": "Snippet two",
        "score": 0.5,
        "raw_content": None,
        "published_date": "Mon, 20 Jan 2025 13:00:00 GMT",
    },
    {
        "url": "https://c.example.org/undated",
        "title": "Undated",
        "content": "Snippet three",
        "score": 0.25,
        "published_date": None,
    },
]


def ok_body(results: list[Any] | None = None) -> dict[str, Any]:
    return {
        "query": "q",
        "results": SAMPLE_RESULTS if results is None else results,
        "response_time": 1.23,
        "request_id": "req-123",
    }


class Recorder:
    """A MockTransport handler that keeps every request and answers from a factory."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda _r: httpx.Response(200, json=ok_body()))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def body(self) -> dict[str, Any]:
        assert len(self.requests) == 1
        return json.loads(self.requests[0].content)


def build(
    handler: Callable[[httpx.Request], httpx.Response] | Recorder | None = None,
    **kwargs: Any,
) -> tuple[TavilyAdapter, Recorder, httpx.AsyncClient]:
    recorder = handler if isinstance(handler, Recorder) else Recorder(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    adapter = TavilyAdapter(KEY, client=client, today=lambda: TODAY, **kwargs)
    return adapter, recorder, client


def respond(status: int, **kwargs: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(status, **kwargs)


def raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


async def run(request: SearchRequest, **kwargs: Any) -> tuple[SearchResult, Recorder]:
    adapter, recorder, client = build(**kwargs)
    async with client:
        return await adapter.search(request), recorder


async def failure(
    handler: Callable[[httpx.Request], httpx.Response], request: SearchRequest | None = None
) -> SearchError:
    adapter, _recorder, client = build(handler)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(request or SearchRequest("best crm"))
    return info.value


# ---------------------------------------------------------------------------------------------
# Identity and the wire format
# ---------------------------------------------------------------------------------------------


def test_adapter_satisfies_the_port_and_declares_capabilities() -> None:
    adapter, _recorder, _client = build()
    assert isinstance(adapter, SearchPort)
    assert adapter.name == "tavily"
    caps = adapter.capabilities
    assert caps.country and caps.language and caps.exact_match and caps.time_range
    assert caps.domain_prefer and caps.domain_restrict  # include_domains_mode: prefer | restrict
    assert caps.page_content


async def test_request_goes_to_the_official_endpoint_with_a_bearer_header() -> None:
    _result, recorder = await run(SearchRequest("best crm for retailers"))
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    assert recorder.body == {
        "query": "best crm for retailers",
        "search_depth": "basic",
        "topic": "general",
        "max_results": 10,
        "include_published_date": True,
    }


async def test_base_url_override_and_trailing_slash() -> None:
    _result, recorder = await run(SearchRequest("q"), base_url="https://proxy.test/tavily/")
    assert str(recorder.requests[0].url) == "https://proxy.test/tavily/search"


async def test_the_given_key_is_used_not_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "from-the-environment")  # secret-scan: allow
    _result, recorder = await run(SearchRequest("q"))
    assert recorder.requests[0].headers["authorization"] == f"Bearer {KEY}"


async def test_timeout_is_passed_to_every_request() -> None:
    _result, recorder = await run(SearchRequest("q"), timeout_s=3.5)
    timeout = recorder.requests[0].extensions["timeout"]
    assert timeout == {"connect": 3.5, "read": 3.5, "write": 3.5, "pool": 3.5}


async def test_blank_or_control_character_query_is_bad_request_without_a_call() -> None:
    adapter, recorder, client = build()
    async with client:
        for query in ("", "   ", "\n\t"):
            with pytest.raises(SearchError) as info:
                await adapter.search(SearchRequest(query))
            assert info.value.kind == "bad_request"
    assert recorder.requests == []


async def test_whitespace_in_the_query_is_collapsed() -> None:
    _result, recorder = await run(SearchRequest("  best \n crm   tools "))
    assert recorder.body["query"] == "best crm tools"


# ---------------------------------------------------------------------------------------------
# Request mapping: country and language
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("in", "india"),
        ("ae", "united arab emirates"),
        ("ke", "kenya"),
        ("gb", "united kingdom"),
        ("us", "united states"),
        ("ng", "nigeria"),
        ("za", "south africa"),
        ("sa", "saudi arabia"),
        ("lk", "sri lanka"),
        ("IN", "india"),
    ],
)
async def test_known_country_is_sent_as_the_lowercase_name(code: str, name: str) -> None:
    result, recorder = await run(SearchRequest("q", country=code))
    assert recorder.body["country"] == name
    assert recorder.body["topic"] == "general"  # country is only valid for the general topic
    assert result.applied["country"] == name
    assert result.locale_weak is False


@pytest.mark.parametrize("code", ["zz", "hk", "india", "i", "1", ""])
async def test_unknown_country_is_not_sent_and_marks_locale_weak(code: str) -> None:
    result, recorder = await run(SearchRequest("q", country=code or None))
    assert "country" not in recorder.body
    assert "country" not in result.applied
    assert result.locale_weak is bool(code)


async def test_language_is_sent_as_a_boost_by_default() -> None:
    result, recorder = await run(SearchRequest("q", language="hi"))
    assert recorder.body["language"] == "hi"
    assert "filter_by_language" not in recorder.body
    assert result.applied["language"] == "hi"
    assert result.locale_weak is False


async def test_language_filter_is_opt_in_at_construction() -> None:
    result, recorder = await run(SearchRequest("q", language="fr"), filter_by_language=True)
    assert recorder.body["language"] == "fr"
    assert recorder.body["filter_by_language"] is True
    assert result.applied["language"] == "fr"


async def test_filter_by_language_is_never_sent_without_a_language() -> None:
    _result, recorder = await run(SearchRequest("q"), filter_by_language=True)
    assert "filter_by_language" not in recorder.body
    assert "language" not in recorder.body


@pytest.mark.parametrize("language", ["hi-Latn", "english", "x", "h i"])
async def test_language_that_is_not_iso_639_1_is_not_sent_and_marks_locale_weak(
    language: str,
) -> None:
    result, recorder = await run(SearchRequest("q", language=language))
    assert "language" not in recorder.body
    assert "language" not in result.applied
    assert result.locale_weak is True


async def test_region_subtag_is_kept_in_lowercase() -> None:
    _result, recorder = await run(SearchRequest("q", language="zh-CN"))
    assert recorder.body["language"] == "zh-cn"  # the form the official docs show


async def test_one_unapplied_locale_part_is_enough_for_locale_weak() -> None:
    result, recorder = await run(SearchRequest("q", country="in", language="hi-Latn"))
    assert recorder.body["country"] == "india"
    assert result.applied == {"country": "india"}
    assert result.locale_weak is True


# ---------------------------------------------------------------------------------------------
# Request mapping: domains, exact match, recency, content, size
# ---------------------------------------------------------------------------------------------


async def test_domain_restrict_sends_include_domains_and_restrict_mode() -> None:
    result, recorder = await run(
        SearchRequest(
            "q",
            domain_mode="restrict",
            domains=("https://www.Gov.in/x", "rbi.org.in", "gov.in", "not a domain!"),
        )
    )
    assert recorder.body["include_domains"] == ["gov.in", "rbi.org.in"]
    assert recorder.body["include_domains_mode"] == "restrict"
    assert result.applied["domain_mode"] == "restrict"
    assert result.applied["domains"] == "gov.in,rbi.org.in"
    assert result.locale_weak is False


async def test_domain_prefer_is_native() -> None:
    result, recorder = await run(
        SearchRequest("q", domain_mode="prefer", domains=("g2.com", "capterra.com"))
    )
    assert recorder.body["include_domains"] == ["g2.com", "capterra.com"]
    assert recorder.body["include_domains_mode"] == "prefer"
    assert result.applied["domain_mode"] == "prefer"


@pytest.mark.parametrize(
    ("mode", "domains"),
    [("none", ("a.com",)), ("restrict", ()), ("prefer", ("not a domain!",)), ("restrict", ("",))],
)
async def test_no_usable_domain_sends_no_domain_parameters(
    mode: Any, domains: tuple[str, ...]
) -> None:
    result, recorder = await run(SearchRequest("q", domain_mode=mode, domains=domains))
    assert "include_domains" not in recorder.body
    assert "include_domains_mode" not in recorder.body
    assert "domain_mode" not in result.applied


async def test_domains_are_capped_at_the_documented_300() -> None:
    domains = tuple(f"site{i}.example.com" for i in range(350))
    result, recorder = await run(SearchRequest("q", domain_mode="restrict", domains=domains))
    assert len(recorder.body["include_domains"]) == 300
    assert result.applied["domain_mode"] == "restrict"


async def test_exact_uses_exact_match_and_keeps_the_quoted_phrase() -> None:
    result, recorder = await run(SearchRequest('GST "rule 86B" limits', exact=True))
    assert recorder.body["exact_match"] is True
    assert recorder.body["query"] == 'GST "rule 86B" limits'  # only the quoted phrase is exact
    assert result.applied["exact"] == "true"


async def test_exact_without_a_quoted_phrase_quotes_the_whole_query() -> None:
    _result, recorder = await run(SearchRequest("rule 86B limits", exact=True))
    assert recorder.body["query"] == '"rule 86B limits"' and recorder.body["exact_match"] is True


async def test_not_exact_leaves_the_query_alone() -> None:
    result, recorder = await run(SearchRequest("plain query"))
    assert "exact_match" not in recorder.body
    assert recorder.body["query"] == "plain query"
    assert "exact" not in result.applied


@pytest.mark.parametrize(
    ("months", "start"),
    [
        (1, "2026-10-11"),
        (3, "2026-08-11"),
        (12, "2025-11-11"),
        (18, "2025-05-11"),
        (24, "2024-11-11"),
    ],
)
async def test_recency_becomes_an_exact_start_date(months: int, start: str) -> None:
    result, recorder = await run(SearchRequest("q", recency_months=months))
    assert recorder.body["start_date"] == start
    assert "time_range" not in recorder.body
    assert "filter_by_published_date" not in recorder.body  # undated sources are kept
    assert result.applied["recency"] == f"{months}m"


@pytest.mark.parametrize("months", [None, 0, -3])
async def test_no_recency_means_no_date_window(months: int | None) -> None:
    result, recorder = await run(SearchRequest("q", recency_months=months))
    assert "start_date" not in recorder.body
    assert "recency" not in result.applied


async def test_absurd_recency_is_capped_instead_of_failing() -> None:
    result, recorder = await run(SearchRequest("q", recency_months=10**12))
    assert recorder.body["start_date"] == "1926-11-11"
    assert result.applied["recency"] == "1200m"


async def test_want_content_asks_for_text_raw_content() -> None:
    _result, recorder = await run(SearchRequest("q", want_content=True))
    assert recorder.body["include_raw_content"] == "text"


async def test_content_is_not_requested_by_default() -> None:
    _result, recorder = await run(SearchRequest("q"))
    assert "include_raw_content" not in recorder.body


@pytest.mark.parametrize(
    ("asked", "sent"), [(10, 10), (1, 1), (20, 20), (50, 20), (0, 1), (-4, 1), (10**9, 20)]
)
async def test_max_results_is_clamped_to_the_documented_range(asked: int, sent: int) -> None:
    _result, recorder = await run(SearchRequest("q", max_results=asked))
    assert recorder.body["max_results"] == sent


@pytest.mark.parametrize("depth", ["basic", "fast", "ultra-fast", "advanced"])
async def test_search_depth_is_configurable(depth: Any) -> None:
    _result, recorder = await run(SearchRequest("q"), search_depth=depth)
    assert recorder.body["search_depth"] == depth


def test_unknown_search_depth_is_a_construction_error() -> None:
    with pytest.raises(ValueError, match="search_depth"):
        TavilyAdapter(KEY, search_depth="turbo")  # type: ignore[arg-type]


async def test_the_full_request_maps_every_parameter_at_once() -> None:
    request = SearchRequest(
        query="mandi licence rules",
        country="in",
        language="hi",
        domain_mode="prefer",
        domains=("agmarknet.gov.in",),
        exact=True,
        recency_months=6,
        want_content=True,
        max_results=5,
    )
    result, recorder = await run(request)
    assert recorder.body == {
        "query": '"mandi licence rules"',
        "search_depth": "basic",
        "topic": "general",
        "max_results": 5,
        "include_published_date": True,
        "exact_match": True,
        "country": "india",
        "language": "hi",
        "include_domains": ["agmarknet.gov.in"],
        "include_domains_mode": "prefer",
        "start_date": "2026-05-11",
        "include_raw_content": "text",
    }
    assert result.applied == {
        "exact": "true",
        "country": "india",
        "language": "hi",
        "domain_mode": "prefer",
        "domains": "agmarknet.gov.in",
        "recency": "6m",
    }
    assert result.locale_weak is False


# ---------------------------------------------------------------------------------------------
# Normalisation of the answer
# ---------------------------------------------------------------------------------------------


async def test_hits_are_normalised_in_provider_order() -> None:
    result, _recorder = await run(SearchRequest("q"))
    assert result.provider == "tavily"
    assert [h.rank for h in result.hits] == [1, 2, 3]
    first, second, third = result.hits
    assert first.url == "https://www.nashik.gov.in/produce-rules"
    assert first.title == "Produce market rules"
    assert first.snippet == "Snippet one [...] more of it"
    assert first.provider_score == 0.91
    assert first.published == date(2025, 3, 12)
    assert second.published == date(2025, 1, 20)  # RFC 2822
    assert third.published is None
    assert second.provider_score == 0.5


async def test_raw_content_passes_through_only_when_asked_for() -> None:
    with_content, _ = await run(SearchRequest("q", want_content=True))
    assert [h.content for h in with_content.hits] == ["FULL PAGE TEXT ONE", None, None]
    without, _ = await run(SearchRequest("q"))
    assert [h.content for h in without.hits] == [None, None, None]


async def test_blank_raw_content_is_none() -> None:
    body = ok_body([{"url": "https://a.com", "content": "s", "raw_content": "   "}])
    result, _ = await run(SearchRequest("q", want_content=True), handler=respond(200, json=body))
    assert result.hits[0].content is None


async def test_hits_without_a_url_are_dropped_and_ranks_stay_contiguous() -> None:
    results = [
        {"url": "https://a.com/1", "title": "one", "content": "s1"},
        {"title": "no url", "content": "x"},
        {"url": "", "title": "empty url"},
        {"url": None, "title": "null url"},
        {"url": 5, "title": "numeric url"},
        "not an object",
        None,
        {"url": "  https://a.com/2  ", "title": "two", "content": "s2"},
    ]
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body(results)))
    assert [(h.rank, h.url) for h in result.hits] == [
        (1, "https://a.com/1"),
        (2, "https://a.com/2"),
    ]


async def test_missing_optional_fields_default_cleanly() -> None:
    result, _ = await run(
        SearchRequest("q"), handler=respond(200, json=ok_body([{"url": "https://a.com"}]))
    )
    (hit,) = result.hits
    assert (hit.title, hit.snippet, hit.content, hit.published, hit.provider_score) == (
        "",
        "",
        None,
        None,
        None,
    )


@pytest.mark.parametrize("score", ["0.9", None, True, float("nan")])
async def test_non_numeric_scores_become_none(score: Any) -> None:
    body = {"results": [{"url": "https://a.com", "score": score}]}
    # NaN is not valid JSON, so send it through text for the nan case.
    raw = json.dumps(body, allow_nan=True)
    result, _ = await run(SearchRequest("q"), handler=respond(200, content=raw))
    assert result.hits[0].provider_score is None


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("2025-03-12", date(2025, 3, 12)),
        ("2025-03-12T08:00:00Z", date(2025, 3, 12)),
        ("3 days ago", date(2026, 11, 8)),  # relative to the injected today, not the real clock
        ("Mar 12, 2025", date(2025, 3, 12)),
        ("not a date", None),
        ("", None),
        (None, None),
        (20250312, None),
    ],
)
async def test_published_dates_are_parsed_or_none(published: Any, expected: date | None) -> None:
    body = ok_body([{"url": "https://a.com", "published_date": published}])
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=body))
    assert result.hits[0].published == expected


async def test_hits_are_trimmed_to_max_results() -> None:
    results = [{"url": f"https://a.com/{i}", "title": str(i)} for i in range(10)]
    result, _ = await run(
        SearchRequest("q", max_results=4), handler=respond(200, json=ok_body(results))
    )
    assert [h.rank for h in result.hits] == [1, 2, 3, 4]
    assert result.hits[-1].url == "https://a.com/3"


async def test_an_empty_result_list_is_a_valid_answer() -> None:
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body([])))
    assert result.hits == ()
    assert result.cost_micros == 8000  # a search that found nothing is still billed


async def test_latency_is_measured_and_cached_is_false() -> None:
    result, _ = await run(SearchRequest("q"))
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0
    assert result.cached is False


# ---------------------------------------------------------------------------------------------
# Cost: integer micro-USD
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("depth", "micros"),
    [("basic", 8000), ("fast", 8000), ("ultra-fast", 8000), ("advanced", 16000)],
)
async def test_cost_is_credits_times_the_per_credit_price(depth: Any, micros: int) -> None:
    result, _ = await run(SearchRequest("q"), search_depth=depth)
    assert result.cost_micros == micros
    assert isinstance(result.cost_micros, int)


async def test_raw_content_adds_no_credits() -> None:
    plain, _ = await run(SearchRequest("q"))
    with_content, _ = await run(SearchRequest("q", want_content=True))
    assert with_content.cost_micros == plain.cost_micros == 8000


async def test_cost_override_wins() -> None:
    result, _ = await run(SearchRequest("q"), cost_per_call_micros=5000)
    assert result.cost_micros == 5000
    free, _ = await run(SearchRequest("q"), cost_per_call_micros=0)
    assert free.cost_micros == 0


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_cost_override_must_be_a_non_negative_integer(bad: Any) -> None:
    with pytest.raises(ValueError, match="cost_per_call_micros"):
        TavilyAdapter(KEY, cost_per_call_micros=bad)


@pytest.mark.parametrize("bad", [0, -1.0])
def test_timeout_must_be_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        TavilyAdapter(KEY, timeout_s=bad)


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limited"),
        (432, "quota"),
        (433, "quota"),
        (434, "rate_limited"),
        (402, "quota"),
        (400, "bad_request"),
        (422, "bad_request"),
        (413, "bad_request"),
        (408, "timeout"),
        (500, "http_error"),
        (502, "http_error"),
        (503, "http_error"),
        (404, "http_error"),
        (302, "http_error"),
    ],
)
async def test_http_status_maps_to_the_error_kind(status: int, kind: str) -> None:
    error = await failure(respond(status, json={"detail": {"error": "something happened"}}))
    assert isinstance(error, SearchError)
    assert error.kind == kind
    assert error.status == status
    assert "something happened" in str(error)


async def test_error_with_a_non_json_body_still_maps() -> None:
    error = await failure(respond(502, content=b"<html>Bad gateway</html>"))
    assert (error.kind, error.status) == ("http_error", 502)


async def test_plan_limit_body_is_quota_even_on_a_400() -> None:
    body = {"detail": {"error": "This request exceeds your plan's set usage limit."}}
    error = await failure(respond(400, json=body))
    assert error.kind == "quota"


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("2.5", 2.5), ("0", 0.0), ("not a number", None), (None, None)],
)
async def test_429_carries_retry_after_seconds(header: str | None, expected: float | None) -> None:
    headers = {"Retry-After": header} if header is not None else {}
    error = await failure(respond(429, headers=headers, json={"detail": {"error": "slow down"}}))
    assert error.kind == "rate_limited"
    assert error.retry_after == expected


async def test_429_retry_after_as_an_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)
    error = await failure(respond(429, headers={"Retry-After": when}))
    assert error.retry_after is not None
    assert 90.0 < error.retry_after <= 120.0


async def test_429_retry_after_in_the_past_is_zero() -> None:
    when = format_datetime(datetime.now(UTC) - timedelta(seconds=300), usegmt=True)
    error = await failure(respond(429, headers={"Retry-After": when}))
    assert error.retry_after == 0.0


async def test_other_errors_carry_no_retry_after() -> None:
    error = await failure(respond(500, headers={"Retry-After": "9"}))
    assert error.retry_after is None


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.WriteTimeout("slow"),
        httpx.PoolTimeout("slow"),
    ],
)
async def test_httpx_timeouts_map_to_timeout(exc: Exception) -> None:
    error = await failure(raising(exc))
    assert error.kind == "timeout"
    assert error.status is None


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("garbled"),
        httpx.ProxyError("proxy"),
        httpx.UnsupportedProtocol("scheme"),
    ],
)
async def test_connection_failures_map_to_network(exc: Exception) -> None:
    error = await failure(raising(exc))
    assert error.kind == "network"


async def test_a_slow_answer_hits_the_total_deadline() -> None:
    never = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await never.wait()
        return httpx.Response(200, json=ok_body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TavilyAdapter(KEY, client=client, timeout_s=0.05, today=lambda: TODAY)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(SearchRequest("q"))
    assert info.value.kind == "timeout"


@pytest.mark.parametrize(
    "handler",
    [
        respond(200, content=b"this is not json"),
        respond(200, content=b""),
        respond(200, content=b'{"results": [ {"url": '),
        respond(200, json=["a", "list"]),
        respond(200, json="a string"),
        respond(200, json=None),
        respond(200, json={}),
        respond(200, json={"query": "q"}),
        respond(200, json={"results": None}),
        respond(200, json={"results": {"url": "https://a.com"}}),
    ],
)
async def test_unusable_200_bodies_are_bad_response(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    error = await failure(handler)
    assert error.kind == "bad_response"


async def test_a_redirect_is_never_followed_with_the_key() -> None:
    hits: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.test/search"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    adapter = TavilyAdapter(KEY, client=client, today=lambda: TODAY)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(SearchRequest("q"))
    assert info.value.kind == "http_error"
    assert info.value.status == 302
    assert len(hits) == 1


async def test_an_unexpected_internal_failure_still_surfaces_as_search_error() -> None:
    error = await failure(raising(RuntimeError("boom with details")))
    assert error.kind == "bad_response"
    assert isinstance(error, SearchError)


async def test_cancellation_is_not_swallowed() -> None:
    adapter, _recorder, client = build(raising(asyncio.CancelledError()))  # pyright: ignore[reportArgumentType]
    async with client:
        with pytest.raises(asyncio.CancelledError):
            await adapter.search(SearchRequest("q"))


# ---------------------------------------------------------------------------------------------
# The key never leaks
# ---------------------------------------------------------------------------------------------


def test_repr_and_str_do_not_contain_the_key() -> None:
    adapter = TavilyAdapter(KEY)
    for text in (repr(adapter), str(adapter), f"{adapter}", f"{adapter!r}", repr([adapter])):
        assert KEY not in text
        assert "tvly" not in text
    assert "TavilyAdapter" in repr(adapter)
    assert KEY not in repr(vars(adapter))
    with pytest.raises(TypeError):
        pickle.dumps(adapter)


@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 432, 433, 500, 503])
async def test_an_error_body_that_echoes_the_key_is_redacted(status: int) -> None:
    body = {"detail": {"error": f"Invalid API key {KEY}: check your dashboard"}}
    error = await failure(respond(status, json=body))
    assert KEY not in str(error)
    assert KEY not in repr(error)
    assert "[redacted]" in str(error)
    assert KEY not in repr(error.__cause__)


async def test_a_422_never_echoes_the_request_input() -> None:
    body = {
        "detail": [
            {
                "loc": ["body", "max_results"],
                "msg": "Input should be less than or equal to 20",
                "type": "less_than_equal",
                "input": "THE-FULL-USER-QUERY-AND-BODY",
                "ctx": {"le": 20},
            }
        ]
    }
    error = await failure(respond(422, json=body), SearchRequest("THE-FULL-USER-QUERY-AND-BODY"))
    assert error.kind == "bad_request"
    assert "less than or equal to 20" in str(error)
    assert "THE-FULL-USER-QUERY-AND-BODY" not in str(error)
    assert KEY not in str(error)


async def test_error_messages_are_short_and_single_line() -> None:
    error = await failure(
        respond(500, json={"detail": {"error": "line one\nline two " + "x" * 5000}})
    )
    assert "\n" not in str(error)
    assert len(str(error)) < 400


@pytest.mark.parametrize("bad_key", ["", "   ", "key with spaces", "key\nline", "kéy"])
async def test_an_unusable_key_is_an_auth_error_before_any_call(bad_key: str) -> None:
    recorder = Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    adapter = TavilyAdapter(bad_key, client=client)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(SearchRequest("q"))
    assert info.value.kind == "auth"
    assert recorder.requests == []
    assert bad_key.strip() == "" or bad_key not in str(info.value)


# ---------------------------------------------------------------------------------------------
# Client ownership
# ---------------------------------------------------------------------------------------------


async def test_a_client_passed_in_is_never_closed_by_the_adapter() -> None:
    adapter, _recorder, client = build()
    await adapter.search(SearchRequest("q"))
    await adapter.aclose()
    assert client.is_closed is False
    async with adapter:
        await adapter.search(SearchRequest("q"))
    assert client.is_closed is False
    await client.aclose()


async def test_the_client_the_adapter_created_is_closed_by_aclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []
    options: list[dict[str, Any]] = []
    recorder = Recorder()

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        options.append(kwargs)
        client = real(transport=httpx.MockTransport(recorder), **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(common.httpx, "AsyncClient", factory)  # pyright: ignore[reportPrivateImportUsage]
    adapter = TavilyAdapter(KEY, timeout_s=4.0, today=lambda: TODAY)
    assert created == []  # nothing is opened until the first search
    await adapter.search(SearchRequest("q"))
    await adapter.search(SearchRequest("q"))
    assert len(created) == 1  # one client, reused
    assert options[0]["trust_env"] is False  # the environment never decides where the key goes
    assert created[0].is_closed is False
    await adapter.aclose()
    assert created[0].is_closed is True
    await adapter.aclose()  # closing twice is harmless


async def test_aclose_before_any_search_is_harmless() -> None:
    await TavilyAdapter(KEY).aclose()


# ---------------------------------------------------------------------------------------------
# Property: only SearchError ever escapes, and only verified parameters are sent
# ---------------------------------------------------------------------------------------------

_TRANSPORTS: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "ok": respond(200, json=ok_body()),
    "empty": respond(200, json=ok_body([])),
    "weird_items": respond(
        200,
        json=ok_body(
            [
                {"url": ["x"], "title": 1, "content": {}, "score": "x", "published_date": 7},
                {"url": "https://a.com", "title": None, "raw_content": 5},
                [],
                "x",
            ]
        ),
    ),
    "bad_json": respond(200, content=b"{{{"),
    "list_body": respond(200, json=[1, 2]),
    "no_results": respond(200, json={"nope": 1}),
    "rate_limited": respond(429, headers={"Retry-After": "3"}, json={"detail": {"error": "x"}}),
    "auth": respond(401, json={"detail": {"error": "no"}}),
    "quota": respond(432, json={"detail": {"error": "over"}}),
    "server": respond(503, content=b"down"),
    "timeout": raising(httpx.ReadTimeout("t")),
    "connect": raising(httpx.ConnectError("c")),
    "runtime": raising(RuntimeError("r")),
}

_TEXTS = st.one_of(
    st.sampled_from(
        [
            "",
            "  ",
            "best crm",
            'say "hi"',
            "हिन्दी 日本語 query",
            "q" * 3000,
            "a\x00b\nc",
            '"',
            '" "',
        ]
    ),
    st.text(max_size=40),
)
_REQUESTS = st.builds(
    SearchRequest,
    query=_TEXTS,
    country=st.one_of(
        st.none(),
        st.sampled_from(["in", "IN", "zz", "india", "", " in ", "gb", "x1"]),
        st.text(max_size=4),
    ),
    language=st.one_of(
        st.none(),
        st.sampled_from(["hi", "hi-Latn", "xx", "", "en_US", "zh-CN", "english"]),
        st.text(max_size=6),
    ),
    domain_mode=st.sampled_from(["none", "prefer", "restrict"]),
    domains=st.lists(
        st.one_of(
            st.sampled_from(
                ["a.com", "https://www.b.com/x", "bad domain!!", "", "x(y).com", "*.gov.in"]
            ),
            st.text(max_size=20),
        ),
        max_size=6,
    ).map(tuple),
    exact=st.booleans(),
    recency_months=st.one_of(st.none(), st.integers(-5, 40), st.integers(0, 10**12)),
    want_content=st.booleans(),
    max_results=st.one_of(st.integers(-3, 30), st.integers(-(10**9), 10**9)),
)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(request=_REQUESTS, transport=st.sampled_from(sorted(_TRANSPORTS)))
def test_property_only_search_error_escapes_and_only_known_parameters_are_sent(
    request: SearchRequest, transport: str
) -> None:
    recorder = Recorder(_TRANSPORTS[transport])

    async def attempt() -> SearchResult | SearchError:
        client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        adapter = TavilyAdapter(KEY, client=client, today=lambda: TODAY)
        async with client:
            try:
                return await adapter.search(request)
            except SearchError as error:
                return error

    outcome = asyncio.run(attempt())
    for sent in recorder.requests:
        body = json.loads(sent.content)
        assert set(body) <= ALLOWED_BODY_KEYS
        assert 1 <= body["max_results"] <= 20
        assert KEY not in sent.content.decode("utf-8")
    if isinstance(outcome, SearchError):
        assert KEY not in str(outcome)
        return
    assert outcome.provider == "tavily"
    assert [h.rank for h in outcome.hits] == list(range(1, len(outcome.hits) + 1))
    assert all(h.url for h in outcome.hits)
    assert isinstance(outcome.cost_micros, int)
    assert outcome.cost_micros >= 0
    assert outcome.latency_ms >= 0
    assert set(outcome.applied) <= {
        "country",
        "language",
        "domain_mode",
        "domains",
        "exact",
        "recency",
    }
    if request.country and "country" not in outcome.applied:
        assert outcome.locale_weak
    if request.language and "language" not in outcome.applied:
        assert outcome.locale_weak
