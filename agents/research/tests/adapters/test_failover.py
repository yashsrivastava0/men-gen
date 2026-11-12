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


class Picky(Stub):
    """Returns nothing for a narrowed request (exact match, recency window, domain restriction), ``hits`` otherwise."""

    async def search(self, request: SearchRequest) -> SearchResult:
        narrowed = request.exact or request.recency_months or request.domain_mode != "none"
        self.calls += 1
        n = 0 if narrowed else self.hits
        return SearchResult(
            hits=tuple(
                SearchHit(url=f"https://{self.name}.example/{i}-{request.query}", rank=i)
                for i in range(n)
            ),
            provider=self.name,
            cost_micros=self.price,
        )

    price = 8000


NARROW = SearchRequest(query='"Acme" features pricing', exact=True, recency_months=24)


async def test_a_narrowed_request_is_retried_relaxed_on_the_primary_before_any_second_opinion() -> (
    None
):
    a, b = Picky("a", hits=5), Stub("b")
    f = FailoverSearch(a, b)
    res = await f.search(NARROW)
    assert a.calls == 2 and b.calls == 0, "the retry rescued it, so the secondary is not paid"
    assert len(res.hits) == 5 and res.provider == "a" and res.cost_micros == 16000
    assert f.relaxed_retries == 1 and f.opinions == 1


async def test_still_thin_after_the_retry_asks_the_second_opinion_with_the_relaxed_request() -> (
    None
):
    seen: list[SearchRequest] = []

    class Spy(Stub):
        async def search(self, request: SearchRequest) -> SearchResult:
            seen.append(request)
            return await super().search(request)

    a, b = Picky("a", hits=1), Spy("b", hits=4)
    f = FailoverSearch(a, b)
    res = await f.search(NARROW)
    assert a.calls == 2 and b.calls == 1
    assert seen[0].exact is False and seen[0].recency_months is None and '"' not in seen[0].query
    assert res.provider == "a+b" and res.cost_micros == 8000 + 8000 + 1000


async def test_a_paid_second_opinion_is_named_even_when_it_adds_no_new_url() -> None:
    """failover.py labelled such a call 'tavily': the Small run booked 3,000 micro-USD of Serper spend under Tavily."""

    class Same(Stub):
        async def search(self, request: SearchRequest) -> SearchResult:
            self.calls += 1
            return SearchResult(
                hits=(SearchHit(url="https://a.example/0-q", rank=0),),
                provider=self.name,
                cost_micros=1000,
            )

    f = FailoverSearch(Stub("a", hits=1), Same("b"))
    res = await f.search(REQ)
    assert len(res.hits) == 1 and res.provider == "a+b" and res.cost_micros == 2000


async def test_relaxing_has_nothing_to_do_for_a_plain_request() -> None:
    from mde_research_agent.adapters.search.failover import relax

    assert relax(SearchRequest(query="plain words")) is None
    assert relax(NARROW).query == "Acme features pricing"  # type: ignore[union-attr]


async def test_a_primary_out_of_quota_or_key_is_skipped_until_the_cool_down_ends() -> None:
    now = [100.0]
    a, b = Stub("a", error=SearchError("quota")), Stub("b")
    f = FailoverSearch(a, b, down_cooldown_s=600, clock=lambda: now[0])
    assert (await f.search(REQ)).provider == "b" and a.calls == 1 and f.primary_down
    for _ in range(3):
        assert (await f.search(REQ)).provider == "b"
    assert a.calls == 1 and f.skipped_while_down == 3  # not asked first again
    assert f.drain_notes() == ["provider_failover"]  # told once, not on every call
    now[0] += 601  # the cool-down is over: one call asks the primary again
    a.error = None
    assert (await f.search(REQ)).provider == "a" and a.calls == 2 and not f.primary_down


async def test_a_slow_or_rate_limited_primary_is_not_taken_out_of_service() -> None:
    a, b = Stub("a", error=SearchError("rate_limited", retry_after=2)), Stub("b")
    f = FailoverSearch(a, b)
    await f.search(REQ)
    await f.search(REQ)
    assert (
        a.calls == 2 and f.skipped_while_down == 0
    )  # only a wrong key or a used-up plan stays down
