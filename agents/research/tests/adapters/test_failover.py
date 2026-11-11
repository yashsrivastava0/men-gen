"""RC-10: failover to the other search provider, and a bounded second opinion; the failure drills of doc 14 section 8."""

from __future__ import annotations

import pytest

from mde_research_agent.adapters.search.failover import FailoverSearch
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchRequest,
    SearchResult,
)


class Stub:
    def __init__(self, name: str, hits: int = 5, error: SearchError | None = None) -> None:
        self.name, self.capabilities = name, Capabilities()
        self.hits, self.error, self.calls = hits, error, 0

    async def search(self, request: SearchRequest) -> SearchResult:
        self.calls += 1
        if self.error:
            raise self.error
        return SearchResult(
            hits=tuple(
                SearchHit(url=f"https://{self.name}.example/{i}-{request.query}", rank=i)
                for i in range(self.hits)
            ),
            provider=self.name,
            cost_micros=1000,
        )


REQ = SearchRequest(query="q")


async def test_a_failing_primary_fails_over_and_the_engine_is_told() -> None:
    a, b = Stub("a", error=SearchError("rate_limited", retry_after=2)), Stub("b")
    f = FailoverSearch(a, b)
    res = await f.search(REQ)
    assert (
        res.provider == "b"
        and f.failovers == 1
        and f.drain_notes() == ["provider_failover"]
        and f.drain_notes() == []
    )


async def test_when_both_providers_fail_the_error_surfaces_for_the_run_to_handle() -> None:
    f = FailoverSearch(Stub("a", error=SearchError("quota")), Stub("b", error=SearchError("auth")))
    with pytest.raises(SearchError) as err:
        await f.search(REQ)
    assert err.value.kind == "auth"


async def test_a_malformed_request_is_not_retried_on_the_other_provider() -> None:
    b = Stub("b")
    f = FailoverSearch(Stub("a", error=SearchError("bad_request")), b)
    with pytest.raises(SearchError) as err:
        await f.search(REQ)
    assert err.value.kind == "bad_request" and b.calls == 0


async def test_thin_results_get_a_second_opinion_but_only_for_a_share_of_searches() -> None:
    a, b = Stub("a", hits=1), Stub("b", hits=4)
    f = FailoverSearch(a, b, opinion_share=0.15)
    results = [await f.search(SearchRequest(query=f"q{i}")) for i in range(40)]
    assert f.opinions <= 0.15 * 40 + 1 and f.opinions >= 1
    merged = [r for r in results if "+" in r.provider]
    assert merged and all(
        len(r.hits) == 5 and r.cost_micros == 2000 for r in merged
    )  # primary first, no duplicates
    assert all(r.hits[0].url.startswith("https://a.") for r in merged)


async def test_a_failing_second_opinion_changes_nothing() -> None:
    f = FailoverSearch(Stub("a", hits=1), Stub("b", error=SearchError("network")))
    res = await f.search(REQ)
    assert res.provider == "a" and len(res.hits) == 1 and f.drain_notes() == []
