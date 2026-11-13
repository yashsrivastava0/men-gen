"""``GuardedSearch``: the last line of the privacy guard, just before the provider is called."""

from __future__ import annotations

import pytest

from mde_research_agent.adapters.search.guarded import GuardedSearch
from mde_research_agent.domain.input import PrivateTerm, PrivateTerms
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchPort,
    SearchRequest,
    SearchResult,
)

TERMS = PrivateTerms(
    terms=(
        PrivateTerm(kind="brand", text="Zzyzx Kraal"),
        PrivateTerm(kind="phone", text="+91 98765 43210"),
    )
)


class Inner:
    name = "inner"
    capabilities = Capabilities(country=True)

    def __init__(self) -> None:
        self.sent: list[SearchRequest] = []
        self.notes = ["slow_provider"]

    async def search(self, request: SearchRequest) -> SearchResult:
        self.sent.append(request)
        return SearchResult(hits=(SearchHit(url="https://example.test/a"),), provider="inner")

    def drain_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out


async def test_a_clean_query_reaches_the_provider_unchanged() -> None:
    inner = Inner()
    guard = GuardedSearch(inner, TERMS)  # type: ignore[arg-type]
    req = SearchRequest(query="class booking features Bengaluru", country="in")
    res = await guard.search(req)
    assert res.provider == "inner" and inner.sent == [req] and guard.blocked == 0


@pytest.mark.parametrize(
    "query", ["Zzyzx Kraal pricing", "who owns 98765 43210", '"zzyzx kraal" "class booking"']
)
async def test_a_private_query_is_refused_before_the_provider_and_never_rewritten(
    query: str,
) -> None:
    inner = Inner()
    seen: list[int] = []
    guard = GuardedSearch(inner, TERMS, on_block=lambda: seen.append(1))  # type: ignore[arg-type]
    with pytest.raises(SearchError) as err:
        await guard.search(SearchRequest(query=query))
    assert err.value.kind == "private_term"
    assert "zzyzx" not in str(err.value).casefold() and "98765" not in str(
        err.value
    )  # never the term itself
    assert inner.sent == [] and guard.blocked == 1 and seen == [1]


def test_the_wrapper_is_a_search_port_that_forwards_everything_else() -> None:
    inner = Inner()
    guard = GuardedSearch(inner, TERMS)  # type: ignore[arg-type]
    assert isinstance(guard, SearchPort)
    assert guard.name == "inner" and guard.capabilities.country is True
    assert guard.drain_notes() == ["slow_provider"]  # type: ignore[attr-defined]  # the inner adapter's own method
    with pytest.raises(AttributeError):
        _ = guard.nope  # type: ignore[attr-defined]
