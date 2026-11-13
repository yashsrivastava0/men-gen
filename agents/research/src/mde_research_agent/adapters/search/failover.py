"""Search with a second provider behind it (RC-10): failover and a limited second opinion.

* **Failover:** when the primary provider errors (rate limit, quota, auth, timeout, network, a bad answer) the same
  request goes to the secondary at once; the engine learns of it through ``drain_notes`` and shows a ``warning`` event.
  When BOTH fail, the secondary's error is raised and the run's own handling applies (``provider_down``, a ``partial``).
  A request the provider rejected as malformed (``bad_request``) is not retried elsewhere: it would fail the same way.
* **Thin results, in this order:** when the primary returns fewer than ``thin_below`` results (1) the same query is retried
  ONCE on the primary with every narrowing removed (no exact match, no recency window, no domain restriction, quotes
  dropped), because an over-constrained request is the usual reason for an empty answer; (2) only if it is still thin is
  the secondary asked (a second opinion). Hits are merged (primary first, no duplicate URL). Both steps share one budget of
  at most ``opinion_share`` of all searches (15%), so the cost stays bounded and the choice of primary (the bake-off) still
  decides the run. A request with nothing to relax goes straight to the second opinion.
The merged result carries the cost of every call it made and names every provider that was paid (``tavily+serper``), even
when the second one added no new URL: the money was spent there. This adapter holds no secret: both providers are built
elsewhere.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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
# A primary that says its key is wrong or its plan is used up will say so again at once: it is skipped for a while
# instead of being tried first on every call (it is tried again after the cool-down, one call is the probe).
DOWN_KINDS: Final = frozenset({"quota", "auth"})
DOWN_COOLDOWN_S: Final = 600.0


class FailoverSearch:
    def __init__(
        self,
        primary: SearchPort,
        secondary: SearchPort,
        *,
        opinion_share: float = OPINION_SHARE,
        thin_below: int = THIN_BELOW,
        down_cooldown_s: float = DOWN_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = primary.name
        self.capabilities: Capabilities = primary.capabilities
        self._share = opinion_share
        self._thin = thin_below
        self.calls = 0
        self.opinions = (
            0  # thin results that got more help (a relaxed retry and/or a second opinion)
        )
        self.relaxed_retries = 0
        self.failovers = 0
        self.skipped_while_down = 0
        self._cooldown = down_cooldown_s
        self._clock = clock
        self._down_until = 0.0
        self._notes: list[str] = []

    def drain_notes(self) -> list[str]:
        """Notes (``provider_failover``) since the last call; the engine turns them into warning events."""
        notes, self._notes = self._notes, []
        return notes

    @property
    def primary_down(self) -> bool:
        return self._clock() < self._down_until

    async def search(self, request: SearchRequest) -> SearchResult:
        self.calls += 1
        if (
            self.primary_down
        ):  # its quota or key just failed: do not ask it first again before the cool-down
            self.skipped_while_down += 1
            return await self.secondary.search(request)
        try:
            first = await self.primary.search(request)
        except SearchError as err:
            if err.kind not in FAILOVER_KINDS:
                raise
            self.failovers += 1
            self._notes.append("provider_failover")
            if err.kind in DOWN_KINDS:
                self._down_until = (
                    self._clock() + self._cooldown
                )  # the run was told at the first failover
            return await self.secondary.search(request)  # both failing raises the secondary's error
        if len(first.hits) >= self._thin or self.opinions >= max(1, int(self._share * self.calls)):
            return first
        self.opinions += 1
        merged = first
        relaxed = relax(request)
        if relaxed is not None:
            try:
                again = await self.primary.search(relaxed)
            except SearchError:
                again = None
            if again is not None:
                merged = _merge(merged, again, paid=self.primary.name)
                self.relaxed_retries += 1
                if len(merged.hits) >= self._thin:
                    return merged
        try:
            other = await self.secondary.search(relaxed or request)
        except SearchError:
            return merged  # a second opinion is a bonus: its failure changes nothing
        return _merge(merged, other, paid=self.secondary.name)


def relax(request: SearchRequest) -> SearchRequest | None:
    """The same search without its narrowing (exact match, recency window, domain restriction, quotes); None when
    nothing would change."""
    plain = " ".join(request.query.replace('"', " ").split())
    loose = replace(
        request,
        query=plain,
        exact=False,
        recency_months=None,
        domain_mode="none",
        domains=(),
    )
    return None if loose == request else loose


def _merge(first: SearchResult, other: SearchResult, *, paid: str) -> SearchResult:
    seen = {canonical_url(h.url) for h in first.hits}
    extra = tuple(h for h in other.hits if canonical_url(h.url) not in seen)
    providers = (
        list(dict.fromkeys([*first.provider.split("+"), paid]))
        if other.cost_micros
        else [first.provider]
    )
    return replace(
        first,
        hits=(*first.hits, *extra),
        cost_micros=first.cost_micros + other.cost_micros,
        provider="+".join(providers),
        # Credits are not a sum across providers: each real adapter is metered below this class and the ledger holds
        # what each one billed, so the merged answer carries no single count.
        credits=None,
    )
