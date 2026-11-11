"""SerperAdapter against hand-written bodies modelled on Serper's published sample responses.

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
from mde_research_agent.adapters.search.serper import SerperAdapter
from mde_research_agent.ports.search import SearchError, SearchPort, SearchRequest, SearchResult

TODAY = date(2026, 11, 11)
KEY = "serper-NOT-A-REAL-KEY-0001"  # secret-scan: allow

ALLOWED_BODY_KEYS = frozenset({"q", "num", "gl", "hl", "tbs", "autocorrect"})

SAMPLE_ORGANIC: list[dict[str, Any]] = [
    {
        "title": "Produce market rules in Nashik",
        "link": "https://www.nashik.gov.in/produce-rules",
        "snippet": "Rules for traders ...",
        "position": 1,
        "sitelinks": [{"title": "Rates", "link": "https://www.nashik.gov.in/rates"}],
    },
    {
        "title": "Dated result",
        "link": "https://b.example.com/guide",
        "snippet": "Second snippet",
        "date": "Mar 10, 2022",
        "position": 2,
    },
    {
        "title": "Relative date",
        "link": "https://c.example.org/news",
        "snippet": "Third snippet",
        "date": "3 days ago",
        "position": 3,
    },
]


def ok_body(organic: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "searchParameters": {"q": "q", "gl": "us", "hl": "en", "num": 10, "type": "search"},
        "organic": SAMPLE_ORGANIC if organic is None else organic,
        "relatedSearches": [{"query": "related"}],
        "credits": 1,
    }
    body.update(extra)
    return body


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
) -> tuple[SerperAdapter, Recorder, httpx.AsyncClient]:
    recorder = handler if isinstance(handler, Recorder) else Recorder(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    adapter = SerperAdapter(KEY, client=client, today=lambda: TODAY, **kwargs)
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


def test_adapter_satisfies_the_port_and_is_honest_about_capabilities() -> None:
    adapter, _recorder, _client = build()
    assert isinstance(adapter, SearchPort)
    assert adapter.name == "serper"
    caps = adapter.capabilities
    assert caps.country and caps.language and caps.exact_match and caps.time_range
    assert caps.domain_restrict  # site: operators
    assert caps.domain_prefer is False  # no native prefer, and none is faked
    assert caps.page_content is False  # links and snippets only


async def test_request_goes_to_the_official_endpoint_with_an_x_api_key_header() -> None:
    _result, recorder = await run(SearchRequest("best crm for retailers"))
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://google.serper.dev/search"
    assert request.headers["x-api-key"] == KEY
    assert "authorization" not in request.headers
    assert request.headers["content-type"] == "application/json"
    assert recorder.body == {"q": "best crm for retailers", "num": 10}


async def test_base_url_override_and_trailing_slash() -> None:
    _result, recorder = await run(SearchRequest("q"), base_url="https://proxy.test/serper/")
    assert str(recorder.requests[0].url) == "https://proxy.test/serper/search"


async def test_the_given_key_is_used_not_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "from-the-environment")  # secret-scan: allow
    _result, recorder = await run(SearchRequest("q"))
    assert recorder.requests[0].headers["x-api-key"] == KEY


async def test_timeout_is_passed_to_every_request() -> None:
    _result, recorder = await run(SearchRequest("q"), timeout_s=2.5)
    assert recorder.requests[0].extensions["timeout"] == {
        "connect": 2.5,
        "read": 2.5,
        "write": 2.5,
        "pool": 2.5,
    }


async def test_blank_query_is_bad_request_without_a_call() -> None:
    adapter, recorder, client = build()
    async with client:
        for query in ("", "  ", "\n"):
            with pytest.raises(SearchError) as info:
                await adapter.search(SearchRequest(query))
            assert info.value.kind == "bad_request"
        with pytest.raises(SearchError):
            await adapter.search(SearchRequest('" "', exact=True))
    assert recorder.requests == []


# ---------------------------------------------------------------------------------------------
# Request mapping: gl and hl
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "gl"), [("in", "in"), ("IN", "in"), (" gb ", "gb"), ("ae", "ae")])
async def test_country_is_sent_as_gl_alpha_2(code: str, gl: str) -> None:
    result, recorder = await run(SearchRequest("q", country=code))
    assert recorder.body["gl"] == gl
    assert result.applied["country"] == gl
    assert result.locale_weak is False


@pytest.mark.parametrize("code", ["india", "i", "1x", "zzz", "i n"])
async def test_a_country_that_is_not_alpha_2_is_not_sent_and_marks_locale_weak(code: str) -> None:
    result, recorder = await run(SearchRequest("q", country=code))
    assert "gl" not in recorder.body
    assert "country" not in result.applied
    assert result.locale_weak is True


@pytest.mark.parametrize(
    ("language", "hl"), [("hi", "hi"), ("HI", "hi"), ("zh-cn", "zh-CN"), ("en_GB", "en-GB")]
)
async def test_language_is_sent_as_hl(language: str, hl: str) -> None:
    result, recorder = await run(SearchRequest("q", language=language))
    assert recorder.body["hl"] == hl
    assert result.applied["language"] == hl
    assert result.locale_weak is False


@pytest.mark.parametrize("language", ["hi-Latn", "english", "x", "h i"])
async def test_an_unusable_language_is_not_sent_and_marks_locale_weak(language: str) -> None:
    result, recorder = await run(SearchRequest("q", language=language))
    assert "hl" not in recorder.body
    assert "language" not in result.applied
    assert result.locale_weak is True


async def test_country_and_language_together() -> None:
    result, recorder = await run(SearchRequest("q", country="in", language="hi"))
    assert (recorder.body["gl"], recorder.body["hl"]) == ("in", "hi")
    assert result.applied == {"country": "in", "language": "hi"}
    assert result.locale_weak is False


# ---------------------------------------------------------------------------------------------
# Request mapping: domains, exact, recency, size
# ---------------------------------------------------------------------------------------------


async def test_restrict_with_one_domain_is_one_site_operator() -> None:
    result, recorder = await run(
        SearchRequest("gst rules", domain_mode="restrict", domains=("gov.in",))
    )
    assert recorder.body["q"] == "site:gov.in gst rules"
    assert result.applied["domain_mode"] == "restrict"
    assert result.applied["domains"] == "gov.in"


async def test_restrict_with_several_domains_groups_the_operators() -> None:
    result, recorder = await run(
        SearchRequest(
            "gst rules",
            domain_mode="restrict",
            domains=("https://www.gov.in/x", "rbi.org.in", "gov.in", "bad domain!"),
        )
    )
    assert recorder.body["q"] == "(site:gov.in OR site:rbi.org.in) gst rules"
    assert result.applied["domains"] == "gov.in,rbi.org.in"


async def test_restrict_uses_at_most_ten_site_operators() -> None:
    domains = tuple(f"site{i}.example.com" for i in range(15))
    result, recorder = await run(SearchRequest("q", domain_mode="restrict", domains=domains))
    assert recorder.body["q"].count("site:") == 10
    assert len(result.applied["domains"].split(",")) == 10


async def test_prefer_is_not_supported_and_not_faked() -> None:
    result, recorder = await run(
        SearchRequest("q", domain_mode="prefer", domains=("g2.com", "capterra.com"))
    )
    assert recorder.body["q"] == "q"
    assert "domain_mode" not in result.applied
    assert "domains" not in result.applied


@pytest.mark.parametrize(
    ("mode", "domains"), [("none", ("a.com",)), ("restrict", ()), ("restrict", ("not a domain!",))]
)
async def test_no_usable_domain_leaves_the_query_alone(mode: Any, domains: tuple[str, ...]) -> None:
    result, recorder = await run(SearchRequest("q", domain_mode=mode, domains=domains))
    assert recorder.body["q"] == "q"
    assert "domain_mode" not in result.applied


async def test_exact_wraps_the_query_in_quotes_and_turns_autocorrect_off() -> None:
    result, recorder = await run(SearchRequest('GST "rule 86B" limits', exact=True))
    assert recorder.body["q"] == '"GST rule 86B limits"'
    assert recorder.body["autocorrect"] is False
    assert result.applied["exact"] == "true"


async def test_exact_and_restrict_combine() -> None:
    _result, recorder = await run(
        SearchRequest("rule 86B", exact=True, domain_mode="restrict", domains=("a.com", "b.com"))
    )
    assert recorder.body["q"] == '(site:a.com OR site:b.com) "rule 86B"'


async def test_autocorrect_is_left_to_google_when_not_exact() -> None:
    _result, recorder = await run(SearchRequest("q"))
    assert "autocorrect" not in recorder.body


@pytest.mark.parametrize(
    ("months", "tbs", "applied"),
    [
        (1, "qdr:m", "1m"),
        (2, "qdr:y", "12m"),  # coarser than asked: a 12-month window
        (3, "qdr:y", "12m"),
        (12, "qdr:y", "12m"),
        (13, "qdr:y", "12m"),  # narrower than asked: Serper has nothing longer than a year
        (36, "qdr:y", "12m"),
    ],
)
async def test_recency_picks_the_closest_tbs_and_reports_the_real_window(
    months: int, tbs: str, applied: str
) -> None:
    result, recorder = await run(SearchRequest("q", recency_months=months))
    assert recorder.body["tbs"] == tbs
    assert result.applied["recency"] == applied


@pytest.mark.parametrize("months", [None, 0, -1])
async def test_no_recency_means_no_tbs(months: int | None) -> None:
    result, recorder = await run(SearchRequest("q", recency_months=months))
    assert "tbs" not in recorder.body
    assert "recency" not in result.applied


async def test_want_content_is_ignored_because_serper_returns_no_page_text() -> None:
    result, recorder = await run(SearchRequest("q", want_content=True))
    assert recorder.body == {"q": "q", "num": 10}
    assert all(h.content is None for h in result.hits)
    assert result.locale_weak is False


@pytest.mark.parametrize(
    ("asked", "sent"), [(10, 10), (1, 1), (5, 5), (20, 20), (100, 100), (500, 100), (0, 1), (-2, 1)]
)
async def test_max_results_is_sent_as_num_clamped_to_1_100(asked: int, sent: int) -> None:
    _result, recorder = await run(SearchRequest("q", max_results=asked))
    assert recorder.body["num"] == sent


async def test_the_full_request_maps_every_parameter_at_once() -> None:
    request = SearchRequest(
        query="mandi licence rules",
        country="in",
        language="hi",
        domain_mode="restrict",
        domains=("agmarknet.gov.in",),
        exact=True,
        recency_months=1,
        want_content=True,
        max_results=7,
    )
    result, recorder = await run(request)
    assert recorder.body == {
        "q": 'site:agmarknet.gov.in "mandi licence rules"',
        "num": 7,
        "autocorrect": False,
        "gl": "in",
        "hl": "hi",
        "tbs": "qdr:m",
    }
    assert result.applied == {
        "exact": "true",
        "domain_mode": "restrict",
        "domains": "agmarknet.gov.in",
        "country": "in",
        "language": "hi",
        "recency": "1m",
    }


# ---------------------------------------------------------------------------------------------
# Normalisation of the answer
# ---------------------------------------------------------------------------------------------


async def test_hits_are_normalised_in_provider_order() -> None:
    result, _recorder = await run(SearchRequest("q"))
    assert result.provider == "serper"
    assert [h.rank for h in result.hits] == [1, 2, 3]
    first, second, third = result.hits
    assert first.url == "https://www.nashik.gov.in/produce-rules"
    assert first.title == "Produce market rules in Nashik"
    assert first.snippet == "Rules for traders ..."
    assert first.published is None
    assert first.content is None
    assert first.provider_score is None  # Serper has no relevance score
    assert second.published == date(2022, 3, 10)
    assert third.published == date(2026, 11, 8)  # "3 days ago" from the injected today


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mar 10, 2022", date(2022, 3, 10)),
        ("Sep 1, 2025", date(2025, 9, 1)),
        ("3 days ago", date(2026, 11, 8)),
        ("2 weeks ago", date(2026, 10, 28)),
        ("1 month ago", date(2026, 10, 11)),
        ("5 hours ago", TODAY),
        ("1 year ago", date(2025, 11, 11)),
        ("2025-01-31", date(2025, 1, 31)),
        ("3 दिन पहले", None),  # localised (hl=hi): unparseable becomes None, never a guess
        ("recently", None),
        (None, None),
        (123, None),
    ],
)
async def test_dates_absolute_relative_or_none(text: Any, expected: date | None) -> None:
    body = ok_body([{"link": "https://a.com", "title": "t", "date": text}])
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=body))
    assert result.hits[0].published == expected


async def test_hits_without_a_link_are_dropped_and_ranks_stay_contiguous() -> None:
    organic = [
        {"title": "one", "link": "https://a.com/1", "snippet": "s1", "position": 1},
        {"title": "no link", "snippet": "x", "position": 2},
        {"title": "empty", "link": "", "position": 3},
        {"title": "null", "link": None, "position": 4},
        {"title": "numeric", "link": 5, "position": 5},
        "not an object",
        None,
        {"title": "two", "link": " https://a.com/2 ", "snippet": "s2", "position": 8},
    ]
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body(organic)))
    assert [(h.rank, h.url) for h in result.hits] == [
        (1, "https://a.com/1"),
        (2, "https://a.com/2"),
    ]


async def test_missing_optional_fields_default_cleanly() -> None:
    result, _ = await run(
        SearchRequest("q"), handler=respond(200, json=ok_body([{"link": "https://a.com"}]))
    )
    (hit,) = result.hits
    assert (hit.title, hit.snippet, hit.content, hit.published, hit.provider_score) == (
        "",
        "",
        None,
        None,
        None,
    )


async def test_hits_are_trimmed_to_max_results() -> None:
    organic = [{"link": f"https://a.com/{i}", "title": str(i)} for i in range(10)]
    result, _ = await run(
        SearchRequest("q", max_results=3), handler=respond(200, json=ok_body(organic))
    )
    assert [h.url for h in result.hits] == ["https://a.com/0", "https://a.com/1", "https://a.com/2"]


async def test_an_empty_organic_list_is_a_valid_answer() -> None:
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body([])))
    assert result.hits == ()


async def test_a_search_that_found_nothing_may_leave_organic_out() -> None:
    body = {"searchParameters": {"q": "q", "type": "search"}, "credits": 1}
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=body))
    assert result.hits == ()
    assert result.cost_micros == 1000


async def test_other_result_blocks_are_ignored() -> None:
    body = ok_body(
        knowledgeGraph={"title": "x"},
        answerBox={"answer": "y"},
        peopleAlsoAsk=[{"question": "z", "link": "https://paa.example.com"}],
    )
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=body))
    assert [h.url for h in result.hits] == [h["link"] for h in SAMPLE_ORGANIC]


async def test_latency_is_measured_and_cached_is_false() -> None:
    result, _ = await run(SearchRequest("q"))
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0
    assert result.cached is False


# ---------------------------------------------------------------------------------------------
# Cost: integer micro-USD
# ---------------------------------------------------------------------------------------------


async def test_ten_results_or_fewer_cost_one_credit() -> None:
    body = {"searchParameters": {"q": "q"}, "organic": []}  # no `credits` in the answer
    for num in (1, 10):
        result, _ = await run(SearchRequest("q", max_results=num), handler=respond(200, json=body))
        assert result.cost_micros == 1000
        assert isinstance(result.cost_micros, int)


async def test_more_than_ten_results_are_booked_at_two_credits_without_a_credits_field() -> None:
    body = {"searchParameters": {"q": "q"}, "organic": []}
    result, _ = await run(SearchRequest("q", max_results=20), handler=respond(200, json=body))
    assert result.cost_micros == 2000


async def test_the_credits_field_of_the_answer_wins_over_the_estimate() -> None:
    result, _ = await run(
        SearchRequest("q", max_results=50), handler=respond(200, json=ok_body(credits=1))
    )
    assert result.cost_micros == 1000
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body(credits=3)))
    assert result.cost_micros == 3000


@pytest.mark.parametrize("credits", [0, -1, 99, "2", 1.5, None, True])
async def test_an_implausible_credits_field_falls_back_to_the_estimate(credits: Any) -> None:
    result, _ = await run(SearchRequest("q"), handler=respond(200, json=ok_body(credits=credits)))
    assert result.cost_micros == 1000


async def test_cost_override_wins() -> None:
    result, _ = await run(SearchRequest("q", max_results=50), cost_per_call_micros=300)
    assert result.cost_micros == 300
    free, _ = await run(SearchRequest("q"), cost_per_call_micros=0)
    assert free.cost_micros == 0


@pytest.mark.parametrize("bad", [-1, 0.5, True])
def test_cost_override_must_be_a_non_negative_integer(bad: Any) -> None:
    with pytest.raises(ValueError, match="cost_per_call_micros"):
        SerperAdapter(KEY, cost_per_call_micros=bad)


@pytest.mark.parametrize("bad", [0, -2.0])
def test_timeout_must_be_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        SerperAdapter(KEY, timeout_s=bad)


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limited"),
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
    error = await failure(
        respond(status, json={"message": "something happened", "statusCode": status})
    )
    assert error.kind == kind
    assert error.status == status
    assert "something happened" in str(error)


@pytest.mark.parametrize("status", [400, 402, 403, 429])
async def test_not_enough_credits_is_quota_whatever_the_status(status: int) -> None:
    error = await failure(
        respond(status, json={"message": "Not enough credits", "statusCode": status})
    )
    assert error.kind == "quota"
    assert error.status == status


async def test_unauthorized_403_is_auth() -> None:
    error = await failure(respond(403, json={"message": "Unauthorized.", "statusCode": 403}))
    assert error.kind == "auth"


async def test_rate_limit_message_on_429_is_not_mistaken_for_quota() -> None:
    error = await failure(respond(429, json={"message": "Too many requests, slow down"}))
    assert error.kind == "rate_limited"


async def test_error_with_a_non_json_body_still_maps() -> None:
    error = await failure(respond(502, content=b"<html>Bad gateway</html>"))
    assert (error.kind, error.status) == ("http_error", 502)


@pytest.mark.parametrize(
    ("header", "expected"), [("7", 7.0), ("1.5", 1.5), ("0", 0.0), ("soon", None), (None, None)]
)
async def test_429_carries_retry_after_seconds(header: str | None, expected: float | None) -> None:
    headers = {"Retry-After": header} if header is not None else {}
    error = await failure(respond(429, headers=headers, json={"message": "slow down"}))
    assert error.kind == "rate_limited"
    assert error.retry_after == expected


async def test_429_retry_after_as_an_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    error = await failure(respond(429, headers={"Retry-After": when}))
    assert error.retry_after is not None
    assert 30.0 < error.retry_after <= 60.0


async def test_429_retry_after_in_the_past_is_zero() -> None:
    when = format_datetime(datetime.now(UTC) - timedelta(seconds=600), usegmt=True)
    error = await failure(respond(429, headers={"Retry-After": when}))
    assert error.retry_after == 0.0


async def test_a_429_without_the_header_has_no_retry_after_and_others_never_do() -> None:
    assert (await failure(respond(429, json={}))).retry_after is None
    assert (await failure(respond(503, headers={"Retry-After": "5"}))).retry_after is None


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
    assert (await failure(raising(exc))).kind == "timeout"


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
    assert (await failure(raising(exc))).kind == "network"


async def test_a_slow_answer_hits_the_total_deadline() -> None:
    never = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await never.wait()
        return httpx.Response(200, json=ok_body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SerperAdapter(KEY, client=client, timeout_s=0.05, today=lambda: TODAY)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(SearchRequest("q"))
    assert info.value.kind == "timeout"


@pytest.mark.parametrize(
    "handler",
    [
        respond(200, content=b"this is not json"),
        respond(200, content=b""),
        respond(200, content=b'{"organic": [ {"link": '),
        respond(200, json=["a", "list"]),
        respond(200, json="a string"),
        respond(200, json=None),
        respond(200, json={}),
        respond(200, json={"unrelated": 1}),
        respond(200, json={"organic": None, "unrelated": 1}),
        respond(200, json={"organic": {"link": "https://a.com"}, "searchParameters": {}}),
    ],
)
async def test_unusable_200_bodies_are_bad_response(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    assert (await failure(handler)).kind == "bad_response"


async def test_a_redirect_is_never_followed_with_the_key() -> None:
    hits: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.test/search"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    adapter = SerperAdapter(KEY, client=client, today=lambda: TODAY)
    async with client:
        with pytest.raises(SearchError) as info:
            await adapter.search(SearchRequest("q"))
    assert (info.value.kind, info.value.status) == ("http_error", 302)
    assert len(hits) == 1


async def test_an_unexpected_internal_failure_still_surfaces_as_search_error() -> None:
    error = await failure(raising(RuntimeError("boom with details")))
    assert error.kind == "bad_response"


async def test_cancellation_is_not_swallowed() -> None:
    adapter, _recorder, client = build(raising(asyncio.CancelledError()))
    async with client:
        with pytest.raises(asyncio.CancelledError):
            await adapter.search(SearchRequest("q"))


# ---------------------------------------------------------------------------------------------
# The key never leaks
# ---------------------------------------------------------------------------------------------


def test_repr_and_str_do_not_contain_the_key() -> None:
    adapter = SerperAdapter(KEY)
    for text in (repr(adapter), str(adapter), f"{adapter}", f"{adapter!r}", repr([adapter])):
        assert KEY not in text
        assert "serper-NOT" not in text
    assert "SerperAdapter" in repr(adapter)
    assert KEY not in repr(vars(adapter))
    with pytest.raises(TypeError):
        pickle.dumps(adapter)


@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 500, 503])
async def test_an_error_body_that_echoes_the_key_is_redacted(status: int) -> None:
    error = await failure(
        respond(status, json={"message": f"Invalid X-API-KEY {KEY}", "statusCode": status})
    )
    assert KEY not in str(error)
    assert KEY not in repr(error)
    assert "[redacted]" in str(error)


async def test_the_key_is_only_ever_in_the_header_never_in_the_body() -> None:
    _result, recorder = await run(SearchRequest("q", country="in", language="hi", exact=True))
    sent = recorder.requests[0]
    assert KEY not in sent.content.decode("utf-8")
    assert KEY not in str(sent.url)


async def test_an_error_never_echoes_the_query() -> None:
    error = await failure(
        respond(400, json={"message": "Query not allowed", "q": "THE-FULL-USER-QUERY"}),
        SearchRequest("THE-FULL-USER-QUERY"),
    )
    assert "THE-FULL-USER-QUERY" not in str(error)
    assert "Query not allowed" in str(error)


@pytest.mark.parametrize("bad_key", ["", "   ", "key with spaces", "key\nline", "kéy"])
async def test_an_unusable_key_is_an_auth_error_before_any_call(bad_key: str) -> None:
    recorder = Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    adapter = SerperAdapter(bad_key, client=client)
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

    monkeypatch.setattr(common.httpx, "AsyncClient", factory)
    adapter = SerperAdapter(KEY, timeout_s=4.0, today=lambda: TODAY)
    assert created == []
    await adapter.search(SearchRequest("q"))
    await adapter.search(SearchRequest("q"))
    assert len(created) == 1
    assert options[0]["trust_env"] is False
    assert created[0].is_closed is False
    await adapter.aclose()
    assert created[0].is_closed is True
    await adapter.aclose()


async def test_aclose_before_any_search_is_harmless() -> None:
    await SerperAdapter(KEY).aclose()


# ---------------------------------------------------------------------------------------------
# Property: only SearchError ever escapes, and only known parameters are sent
# ---------------------------------------------------------------------------------------------

_TRANSPORTS: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "ok": respond(200, json=ok_body()),
    "empty": respond(200, json=ok_body([])),
    "no_organic": respond(200, json={"searchParameters": {}, "credits": 1}),
    "weird_items": respond(
        200,
        json=ok_body(
            [
                {"link": ["x"], "title": 1, "snippet": {}, "date": 7},
                {"link": "https://a.com", "title": None, "date": "3 weeks ago"},
                [],
                "x",
            ],
            credits="many",
        ),
    ),
    "bad_json": respond(200, content=b"{{{"),
    "list_body": respond(200, json=[1, 2]),
    "no_fields": respond(200, json={"nope": 1}),
    "rate_limited": respond(429, headers={"Retry-After": "3"}, json={"message": "x"}),
    "auth": respond(403, json={"message": "Unauthorized."}),
    "quota": respond(400, json={"message": "Not enough credits"}),
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
        max_size=12,
    ).map(tuple),
    exact=st.booleans(),
    recency_months=st.one_of(st.none(), st.integers(-5, 40), st.integers(0, 10**12)),
    want_content=st.booleans(),
    max_results=st.one_of(st.integers(-3, 120), st.integers(-(10**9), 10**9)),
)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(request=_REQUESTS, transport=st.sampled_from(sorted(_TRANSPORTS)))
def test_property_only_search_error_escapes_and_only_known_parameters_are_sent(
    request: SearchRequest, transport: str
) -> None:
    recorder = Recorder(_TRANSPORTS[transport])

    async def attempt() -> SearchResult | SearchError:
        client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        adapter = SerperAdapter(KEY, client=client, today=lambda: TODAY)
        async with client:
            try:
                return await adapter.search(request)
            except SearchError as error:
                return error

    outcome = asyncio.run(attempt())
    for sent in recorder.requests:
        body = json.loads(sent.content)
        assert set(body) <= ALLOWED_BODY_KEYS
        assert 1 <= body["num"] <= 100
        assert KEY not in sent.content.decode("utf-8")
        assert sent.headers["x-api-key"] == KEY
    if isinstance(outcome, SearchError):
        assert KEY not in str(outcome)
        return
    assert outcome.provider == "serper"
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
    if request.domain_mode == "prefer":
        assert "domain_mode" not in outcome.applied  # never faked
