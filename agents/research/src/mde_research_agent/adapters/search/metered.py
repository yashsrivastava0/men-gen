"""``MeteredSearch``: books the credits a provider billed for each search into the global credit ledger.

It wraps ONE real adapter (``build_ports`` does that for Tavily and Serper) and sits BELOW the failover and the cache:

* a cache hit never reaches it, so a cached answer costs 0 credits;
* a failover or a second opinion calls each real adapter through its own wrapper, so each credit is booked against the
  provider that billed it;
* ping, the bake-off and the live runs all build their searches through ``build_ports``, so they are all counted.

The credits of a call come from the provider's answer (``SearchResult.credits``: Tavily ``usage.credits`` after
``include_usage``, Serper ``credits``). When the answer carried no count the price table is the fallback
(``provider_prices.yaml``: Tavily ``credits_per_search.basic`` plus the raw-content surcharge, Serper
``credits_per_query``, or ``credits_when_num_over_10`` for more than ``num_threshold`` results). A call that raised
``SearchError`` books nothing: providers do not bill failed calls, and the ledger counts what was billed, so a retry
that succeeds is counted again, exactly as the provider counts it.

The run that is booking comes from ``run_id_getter`` (by default the run of the current task, see
``spend.credits.current_run_id``); outside a run the credits are counted without a run. Booking is advisory and can
never break a search: any ledger failure is swallowed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import replace

from mde_research_agent.adapters.search.common import load_prices, price_int
from mde_research_agent.ports.search import Capabilities, SearchPort, SearchRequest, SearchResult
from mde_research_agent.spend.credits import CreditLedger, current_run_id


def table_credits(provider: str, request: SearchRequest) -> int:
    """What the price table says one call of ``provider`` bills (the fallback when an answer carries no count)."""
    prices = load_prices().get(provider)
    if prices is None:
        return 1
    if provider == "tavily":
        extra = price_int(prices, "raw_content_extra_credits") if request.want_content else 0
        return price_int(prices, "credits_per_search", "basic") + extra
    heavy = request.max_results > price_int(prices, "num_threshold")
    return price_int(prices, "credits_when_num_over_10" if heavy else "credits_per_query")


class MeteredSearch:
    def __init__(
        self,
        inner: SearchPort,
        ledger: CreditLedger,
        provider: str | None = None,
        run_id_getter: Callable[[], str | None] | None = None,
    ) -> None:
        self.inner = inner
        self.ledger = ledger
        self.name = inner.name
        self.capabilities: Capabilities = inner.capabilities
        self.provider = provider or inner.name
        self._run_id = run_id_getter or current_run_id
        getter = getattr(inner, "key_fingerprint", None)
        found = getter() if callable(getter) else None
        self._fingerprint: str | None = found if isinstance(found, str) else None

    def key_fingerprint(self) -> str | None:
        return self._fingerprint

    async def search(self, request: SearchRequest) -> SearchResult:
        result = await self.inner.search(
            request
        )  # a SearchError passes through: nothing was billed
        credits = result.credits
        if credits is None or isinstance(credits, bool) or credits < 0:
            credits = table_credits(self.provider, request)
        with contextlib.suppress(Exception):  # advisory: a ledger problem never breaks a search
            await asyncio.to_thread(
                self.ledger.add_credits,
                self.provider,
                credits,
                self._run_id(),
                fingerprint=self._fingerprint,
            )
        return replace(result, credits=credits)
