"""An in-process search cache: the same search is not paid for twice.

Keyed by normalised query + country + language + domain mode/domains + exact + a freshness bucket; a hit is returned
with ``cached=True`` and ``cost_micros=0``. TTL: 14 days for general facts, 2 days when the query is about prices or fees
(``prevalence.yaml`` fee words). Shared across programs on purpose (owner decision: baseline privacy only). Failures are
never cached. Bounded size (oldest entries dropped).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace

from mde_research_agent.core.text import tokens
from mde_research_agent.ports.search import Capabilities, SearchPort, SearchRequest, SearchResult

GENERAL_TTL_S = 14 * 24 * 3600
FEE_TTL_S = 2 * 24 * 3600


class CachingSearch:
    def __init__(
        self,
        inner: SearchPort,
        *,
        fee_words: tuple[str, ...] = (),
        clock: Callable[[], float] = time.time,
        max_entries: int = 2000,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.capabilities: Capabilities = inner.capabilities
        self._fee = {w.casefold() for w in fee_words}
        self._clock = clock
        self._max = max_entries
        self._store: OrderedDict[tuple[object, ...], tuple[float, SearchResult]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _key(self, r: SearchRequest) -> tuple[object, ...]:
        bucket = None if r.recency_months is None else r.recency_months
        return (
            " ".join(sorted(tokens(r.query))),
            r.country,
            r.language,
            r.domain_mode,
            tuple(sorted(r.domains)),
            r.exact,
            bucket,
            r.want_content,
            r.max_results,
        )

    def _ttl(self, r: SearchRequest) -> int:
        return FEE_TTL_S if self._fee & set(tokens(r.query)) else GENERAL_TTL_S

    async def search(self, request: SearchRequest) -> SearchResult:
        key = self._key(request)
        now = self._clock()
        got = self._store.get(key)
        if got is not None and now - got[0] < self._ttl(request):
            self.hits += 1
            self._store.move_to_end(key)
            return replace(got[1], cached=True, cost_micros=0)
        self.misses += 1
        result = await self.inner.search(request)  # a failure raises SearchError and is not cached
        self._store[key] = (now, result)
        while len(self._store) > self._max:
            self._store.popitem(last=False)
        return result
