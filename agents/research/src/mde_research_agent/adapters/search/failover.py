"""Search with a second provider behind it (RC-10): failover and a limited second opinion.

* **Failover:** when the primary provider errors (rate limit, quota, auth, timeout, network, a bad answer) the same
  request goes to the secondary at once; the engine learns of it through ``drain_notes`` and shows a ``warning`` event.
  When BOTH fail, the secondary's error is raised and the run's own handling applies (``provider_down``, a ``partial``).
  A request the provider rejected as malformed (``bad_request``) is not retried elsewhere: it would fail the same way.
* **Second opinion:** when the primary returns fewer than ``thin_below`` results the secondary is asked too and the hits are
  merged (primary first, no duplicate URL), but only for at most ``opinion_share`` of all searches (15%), so the cost stays
  bounded and the choice of primary (the bake-off) still decides the run.
The merged result carries the cost of both calls. This adapter holds no secret: both providers are built elsewhere.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from mde_research_agent.core.urls import canonical_url
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchPort,
    SearchRequest,
    SearchResult,
)

FAILOVER_KINDS: Final = frozenset(
    {"rate_limited", "quota", "auth", "timeout", "network", "http_error", "bad_response"}
)
OPINION_SHARE: Final = 0.15
THIN_BELOW: Final = 3


class FailoverSearch:
    def __init__(
        self,
        primary: SearchPort,
        secondary: SearchPort,
        *,
        opinion_share: float = OPINION_SHARE,
        thin_below: int = THIN_BELOW,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = primary.name
        self.capabilities: Capabilities = primary.capabilities
        self._share = opinion_share
        self._thin = thin_below
        self.calls = 0
        self.opinions = 0
        self.failovers = 0
        self._notes: list[str] = []

    def drain_notes(self) -> list[str]:
        """Notes (``provider_failover``) since the last call; the engine turns them into warning events."""
        notes, self._notes = self._notes, []
        return notes

    async def search(self, request: SearchRequest) -> SearchResult:
        self.calls += 1
        try:
            first = await self.primary.search(request)
        except SearchError as err:
            if err.kind not in FAILOVER_KINDS:
                raise
            self.failovers += 1
            self._notes.append("provider_failover")
            return await self.secondary.search(request)  # both failing raises the secondary's error
        if len(first.hits) >= self._thin or self.opinions >= max(1, int(self._share * self.calls)):
            return first
        try:
            other = await self.secondary.search(request)
        except SearchError:
            return first  # a second opinion is a bonus: its failure changes nothing
        self.opinions += 1
        seen = {canonical_url(h.url) for h in first.hits}
        extra = tuple(h for h in other.hits if canonical_url(h.url) not in seen)
        merged = replace(
            first,
            hits=(*first.hits, *extra),
            cost_micros=first.cost_micros + other.cost_micros,
            provider=f"{first.provider}+{other.provider}" if extra else first.provider,
        )
        return merged
