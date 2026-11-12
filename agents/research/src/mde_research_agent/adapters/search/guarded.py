"""The last line of the privacy guard: a ``SearchPort`` wrapper that refuses a query naming something private.

The first line is the query gate in ``engine/worker.py`` (``_check``): a variant or a follow-up that names a private
term never becomes a ``SearchRequest``, and another wording takes its place. This wrapper catches what could skip that
gate (a code path that builds a request itself) by RAISING a typed error just before the provider is called. It never
rewrites or replaces a query: what was recorded as sent is exactly what would have been sent.

The error is a ``SearchError`` of kind ``private_term``, so every caller already handles it like a failed search (no retry,
counted as a failure of reason ``private_term``). The private terms themselves are never put in the error text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mde_research_agent.core.privacy import find_private
from mde_research_agent.domain.input import PrivateTerms
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchPort,
    SearchRequest,
    SearchResult,
)


class GuardedSearch:
    """Wraps a :class:`SearchPort`; ``on_block`` is called once per refused query (a counter, never the text)."""

    def __init__(
        self,
        inner: SearchPort,
        terms: PrivateTerms,
        *,
        on_block: Callable[[], None] | None = None,
    ) -> None:
        self._inner = inner
        self._terms = terms
        self._on_block = on_block
        self.name: str = inner.name
        self.capabilities: Capabilities = inner.capabilities
        self.blocked = 0

    def __getattr__(self, attr: str) -> Any:
        # everything else (drain_notes, ping, ...) is the inner adapter's own
        return getattr(self._inner, attr)

    async def search(self, request: SearchRequest) -> SearchResult:
        if find_private(request.query, self._terms):
            self.blocked += 1
            if self._on_block is not None:
                self._on_block()
            raise SearchError("private_term", "the query names something private from the Brief")
        return await self._inner.search(request)
