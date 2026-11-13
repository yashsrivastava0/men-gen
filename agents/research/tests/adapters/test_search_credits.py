"""Provider credits at the search seam: what each adapter reads from an answer, what ``MeteredSearch`` books into the
ledger (cache hits cost nothing, a failover is booked against the provider that billed it), and Tavily's own count
(``GET /usage``) over a mock transport. No network, no key."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from mde_research_agent.adapters.search.caching import CachingSearch
from mde_research_agent.adapters.search.failover import FailoverSearch
from mde_research_agent.adapters.search.metered import MeteredSearch, table_credits
from mde_research_agent.adapters.search.serper import SerperAdapter
from mde_research_agent.adapters.search.tavily import TavilyAdapter
from mde_research_agent.adapters.search.usage import fetch_tavily_usage, parse_usage
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchRequest,
    SearchResult,
)
from mde_research_agent.spend.credits import FILE_NAME, CreditLedger, key_fingerprint

KEY = "tvly-NOT-A-REAL-KEY-0002"  # secret-scan: allow
SKEY = "serper-NOT-A-REAL-KEY-0002"  # secret-scan: allow
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
RESULTS = [{"url": "https://a.example/x", "title": "A", "content": "snippet", "score": 0.9}]


def tavily(
    answer: dict[str, Any] | None = None, status: int = 200
) -> tuple[TavilyAdapter, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, json=answer if answer is not None else {"results": RESULTS})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TavilyAdapter(KEY, client=client, today=lambda: TODAY), sent


def serper(answer: dict[str, Any]) -> SerperAdapter:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=answer))
    )
    return SerperAdapter(SKEY, client=client, today=lambda: TODAY)


def ledger(tmp_path: Path) -> CreditLedger:
    return CreditLedger(tmp_path / FILE_NAME)


def booked(led: CreditLedger, provider: str) -> int:
    return int(led.snapshot()["providers"][provider]["counted_since_baseline"])


# ------------------------------------------------------------------------------------------------ the answers
async def test_tavily_asks_for_usage_and_reads_the_credits_of_the_answer() -> None:
    adapter, sent = tavily({"results": RESULTS, "usage": {"credits": 2}})
    result = await adapter.search(SearchRequest("mandi licence"))
    assert sent[0]["include_usage"] is True
    assert (
        result.credits == 2 and result.cost_micros == 8000
    )  # money is still booked from the price table


@pytest.mark.parametrize(
    "usage", [None, {}, {"credits": None}, {"credits": -1}, {"credits": "2"}, {"credits": True}, 7]
)
async def test_tavily_without_a_usable_count_leaves_credits_unset(usage: Any) -> None:
    body: dict[str, Any] = {"results": RESULTS}
    if usage is not None:
        body["usage"] = usage
    adapter, _ = tavily(body)
    assert (await adapter.search(SearchRequest("q"))).credits is None


async def test_tavily_accepts_a_whole_number_sent_as_a_float() -> None:
    adapter, _ = tavily({"results": RESULTS, "usage": {"credits": 1.0}})
    assert (await adapter.search(SearchRequest("q"))).credits == 1


async def test_serper_reads_the_credits_of_the_answer() -> None:
    result = await serper({"organic": [], "credits": 2}).search(SearchRequest("q"))
    assert result.credits == 2


@pytest.mark.parametrize("credits", [None, 0, 99, "2", 1.5, True])
async def test_serper_without_a_usable_count_leaves_credits_unset(credits: Any) -> None:
    result = await serper({"organic": [], "credits": credits}).search(SearchRequest("q"))
    assert result.credits is None


def test_the_search_result_still_builds_the_old_way() -> None:
    result = SearchResult(hits=(), provider="x")
    assert result.credits is None
    assert SearchResult(hits=(), provider="x", cost_micros=1, cached=True).credits is None


# ------------------------------------------------------------------------------------------------ the meter
async def test_a_search_books_the_credits_the_answer_names(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    adapter, _ = tavily({"results": RESULTS, "usage": {"credits": 2}})
    metered = MeteredSearch(adapter, led, "tavily", lambda: "rrn_1")
    assert metered.name == "tavily" and metered.capabilities.country
    result = await metered.search(SearchRequest("q"))
    assert result.credits == 2 and booked(led, "tavily") == 2
    assert led.run_credits("rrn_1") == {"tavily": 2}
    rec = led.snapshot()["providers"]["tavily"]
    assert rec["key_fingerprint"] == key_fingerprint(
        KEY
    )  # the adapter's own fingerprint, never the key
    assert KEY not in (tmp_path / FILE_NAME).read_text(encoding="utf-8")


async def test_serper_credits_are_booked_to_serper(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    metered = MeteredSearch(serper({"organic": [], "credits": 2}), led, "serper", lambda: "rrn_9")
    await metered.search(SearchRequest("q"))
    assert led.run_credits("rrn_9") == {"serper": 2} and booked(led, "tavily") == 0


async def test_without_a_count_in_the_answer_the_price_table_is_used(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    adapter, _ = tavily({"results": RESULTS})  # no usage in the answer
    metered = MeteredSearch(adapter, led, "tavily", lambda: None)
    result = await metered.search(SearchRequest("q"))
    assert result.credits == 1 and booked(led, "tavily") == 1
    snap = led.snapshot()["providers"]["tavily"]
    assert snap["counted_outside_runs"] == 1 and snap["runs"] == []  # no run: counted, in no run


def test_the_price_table_fallback() -> None:
    assert table_credits("tavily", SearchRequest("q")) == 1
    assert (
        table_credits("tavily", SearchRequest("q", want_content=True)) == 1
    )  # no surcharge is documented
    assert table_credits("serper", SearchRequest("q")) == 1
    assert table_credits("serper", SearchRequest("q", max_results=50)) == 2
    assert table_credits("somebody-else", SearchRequest("q")) == 1


async def test_a_failed_search_books_nothing(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    adapter, _ = tavily({"detail": "nope"}, status=500)
    metered = MeteredSearch(adapter, led, "tavily", lambda: "rrn_1")
    with pytest.raises(SearchError):
        await metered.search(SearchRequest("q"))
    assert booked(led, "tavily") == 0 and not (tmp_path / FILE_NAME).exists()


async def test_a_broken_ledger_never_breaks_a_search(tmp_path: Path) -> None:
    class Broken(CreditLedger):
        def add_credits(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("ledger is on fire")

    adapter, _ = tavily({"results": RESULTS, "usage": {"credits": 1}})
    metered = MeteredSearch(adapter, Broken(tmp_path / FILE_NAME), "tavily")
    assert (await metered.search(SearchRequest("q"))).credits == 1


async def test_the_run_is_the_task_that_is_searching(tmp_path: Path) -> None:
    import asyncio

    from mde_research_agent.spend.credits import set_current_run

    led = ledger(tmp_path)
    adapter, _ = tavily({"results": RESULTS, "usage": {"credits": 1}})
    metered = MeteredSearch(adapter, led, "tavily")  # the default getter: the current task's run

    async def run(rid: str, n: int) -> None:
        set_current_run(rid)
        for _ in range(n):
            await metered.search(SearchRequest("q"))

    await asyncio.gather(asyncio.create_task(run("rrn_a", 3)), asyncio.create_task(run("rrn_b", 2)))
    assert led.run_credits("rrn_a") == {"tavily": 3} and led.run_credits("rrn_b") == {"tavily": 2}


class Stub:
    """A real-looking provider for the stacking tests: answers N hits, bills ``credits``, or fails."""

    def __init__(
        self,
        name: str,
        hits: int = 5,
        credits: int | None = 1,
        error: SearchError | None = None,
    ) -> None:
        self.name, self.capabilities = name, Capabilities()
        self.hits, self.credits, self.error, self.calls = hits, credits, error, 0

    async def search(self, request: SearchRequest) -> SearchResult:
        self.calls += 1
        if self.error:
            raise self.error
        return SearchResult(
            hits=tuple(
                SearchHit(url=f"https://{self.name}.example/{i}", rank=i) for i in range(self.hits)
            ),
            provider=self.name,
            cost_micros=1000,
            credits=self.credits,
        )


async def test_a_cache_hit_costs_zero_credits(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    inner = Stub("tavily", credits=2)
    stack = CachingSearch(MeteredSearch(inner, led, "tavily", lambda: "rrn_1"))
    first = await stack.search(SearchRequest("same question"))
    again = await stack.search(SearchRequest("same question"))
    assert inner.calls == 1 and first.credits == 2 and again.cached and again.credits == 0
    assert booked(led, "tavily") == 2  # the second answer came from the cache: nothing was billed


async def test_a_failover_books_each_credit_to_the_provider_that_billed_it(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    primary = Stub("tavily", error=SearchError("rate_limited"))
    other = Stub("serper", credits=1)
    stack = FailoverSearch(
        MeteredSearch(primary, led, "tavily", lambda: "rrn_1"),
        MeteredSearch(other, led, "serper", lambda: "rrn_1"),
    )
    result = await stack.search(SearchRequest("q"))
    assert result.provider == "serper"
    assert led.run_credits("rrn_1") == {"serper": 1}  # the failed primary billed nothing


async def test_a_second_opinion_is_billed_by_both_providers(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    thin = Stub(
        "tavily", hits=1, credits=1
    )  # a thin answer: a relaxed retry, then a second opinion
    other = Stub("serper", hits=4, credits=1)
    stack = FailoverSearch(
        MeteredSearch(thin, led, "tavily", lambda: "rrn_1"),
        MeteredSearch(other, led, "serper", lambda: "rrn_1"),
    )
    request = SearchRequest("q", exact=True)  # something to relax, so the primary is asked twice
    result = await stack.search(request)
    assert (
        result.provider == "tavily+serper" and result.credits is None
    )  # no single count across providers
    assert led.run_credits("rrn_1") == {
        "tavily": 2,
        "serper": 1,
    }  # what each one billed, retries included


# ------------------------------------------------------------------------------------------------ GET /usage
USAGE_BODY = {
    "key": {"usage": 41, "limit": None, "search_usage": 41, "extract_usage": 0},
    "account": {
        "current_plan": "Researcher",
        "plan_usage": 93,
        "plan_limit": 1000,
        "paygo_usage": 0,
        "paygo_limit": None,
    },
}


def usage_client(
    respond: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return respond(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_usage_reads_the_account_figures_with_a_bearer_header() -> None:
    seen: list[httpx.Request] = []
    async with usage_client(lambda _r: httpx.Response(200, json=USAGE_BODY), seen) as client:
        out = await fetch_tavily_usage(KEY, client, now=NOW)
    assert out.error is None and out.reading == {
        "used": 93,
        "limit": 1000,
        "plan": "Researcher",
        "read_at": NOW.isoformat(),
        "source": "provider",
        "scope": "account",
    }
    (request,) = seen
    assert request.method == "GET" and str(request.url) == "https://api.tavily.com/usage"
    assert (
        request.headers["authorization"] == f"Bearer {KEY}"
        and "x-project-id" not in request.headers
    )


async def test_usage_falls_back_to_the_keys_own_figures() -> None:
    body = {"key": {"usage": 41, "limit": 500}, "account": {"current_plan": None}}
    async with usage_client(lambda _r: httpx.Response(200, json=body)) as client:
        out = await fetch_tavily_usage(KEY, client, now=NOW)
    assert out.reading is not None
    assert (out.reading["used"], out.reading["limit"], out.reading["scope"]) == (41, 500, "key")
    assert out.reading["plan"] is None


async def test_usage_sends_the_project_header_when_asked() -> None:
    seen: list[httpx.Request] = []
    async with usage_client(lambda _r: httpx.Response(200, json=USAGE_BODY), seen) as client:
        await fetch_tavily_usage(KEY, client, project_id="proj-1")
    assert seen[0].headers["x-project-id"] == "proj-1"


async def test_usage_401_is_an_auth_word_not_an_exception() -> None:
    async with usage_client(
        lambda _r: httpx.Response(401, json={"detail": {"error": "bad key"}})
    ) as client:
        out = await fetch_tavily_usage(KEY, client)
    assert out.reading is None and out.error == "auth" and out.status == 401


async def test_usage_429_carries_retry_after() -> None:
    reply = httpx.Response(429, headers={"Retry-After": "120"}, json={"message": "slow down"})
    async with usage_client(lambda _r: reply) as client:
        out = await fetch_tavily_usage(KEY, client)
    assert (out.error, out.retry_after, out.status) == ("rate_limited", 120.0, 429)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json", headers={"content-type": "application/json"}),
        httpx.Response(200, json=[1, 2, 3]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"account": {"plan_usage": "lots"}}),
        httpx.Response(200, json={"account": {"plan_usage": -5}}),
        httpx.Response(200, json={"account": {"plan_usage": True}}),
    ],
)
async def test_usage_with_a_malformed_body_is_a_bad_response(response: httpx.Response) -> None:
    async with usage_client(lambda _r: response) as client:
        out = await fetch_tavily_usage(KEY, client)
    assert out.reading is None and out.error == "bad_response"


async def test_usage_never_raises_on_a_network_failure_and_never_shows_the_key() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach host with {KEY}")

    async with usage_client(boom) as client:
        out = await fetch_tavily_usage(KEY, client)
    assert out.reading is None and out.error == "network" and KEY not in repr(out)

    def slow(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    async with usage_client(slow) as client:
        assert (await fetch_tavily_usage(KEY, client)).error == "timeout"
    async with usage_client(lambda _r: httpx.Response(500, text="oops")) as client:
        assert (await fetch_tavily_usage(KEY, client)).error == "http_error"
    async with usage_client(lambda _r: httpx.Response(200, json=USAGE_BODY)) as client:
        assert (
            await fetch_tavily_usage("   ", client)
        ).error == "auth"  # an empty key is never sent


def test_parse_usage_accepts_whole_floats_only() -> None:
    assert parse_usage({"account": {"plan_usage": 5.0, "plan_limit": 10.0}}, now=NOW) is not None
    assert parse_usage({"account": {"plan_usage": 5.5}}, now=NOW) is None


# ------------------------------------------------------------------------------------------------ the wiring
def test_build_ports_meters_each_real_adapter_into_the_ledger_it_is_given(tmp_path: Path) -> None:
    from pydantic import SecretStr

    from mde_research_agent.compose import build_ports
    from mde_research_agent.config import ResearchSettings

    cfg = ResearchSettings(tavily_api_key=SecretStr(KEY), serper_api_key=SecretStr(SKEY))
    led = ledger(tmp_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    for name, inner in (("tavily", TavilyAdapter), ("serper", SerperAdapter)):
        search, _fetch, _closers = build_ports(name, cfg, client=client, credits=led)
        assert isinstance(search, MeteredSearch) and isinstance(search.inner, inner)
        assert search.ledger is led and search.provider == name
        assert search.key_fingerprint() == key_fingerprint(KEY if name == "tavily" else SKEY)
    none, _fetch, _closers = build_ports("none", cfg, client=client, credits=led)
    assert none is None
    keyless = ResearchSettings(tavily_api_key=None, serper_api_key=None)
    assert build_ports("tavily", keyless, client=client, credits=led)[0] is None


def test_the_default_ledger_is_in_the_data_folder_and_tests_never_use_the_owners(
    tmp_path: Path,
) -> None:
    from mde_research_agent.compose import credit_ledger
    from mde_research_agent.config import ResearchSettings, data_dir, repo_root

    here = ResearchSettings(research_data_dir=tmp_path / "mine")
    assert credit_ledger(here).path == tmp_path / "mine" / FILE_NAME
    # the suite's autouse fixture points the default somewhere private: never the owner's runtime folder
    default = data_dir(ResearchSettings())
    assert not default.is_relative_to(repo_root() / "agents" / "research" / ".runtime")
    assert credit_ledger(ResearchSettings()).path.parent == default


def test_every_adapter_the_app_builds_exposes_a_one_way_fingerprint() -> None:
    adapter, _ = tavily()
    fp = adapter.key_fingerprint()
    assert fp == key_fingerprint(KEY) and len(fp) == 12 and KEY not in fp
    assert KEY not in repr(adapter) and KEY not in str(adapter)
