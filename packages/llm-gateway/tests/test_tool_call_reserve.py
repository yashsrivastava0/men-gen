"""A tool's call cap is best effort at the API, so the reservation covers twice the cap (D-082).

Live batch L2 saw one web search billed two tool calls under ``max_tool_calls=1``: USD 0.0206 against
a USD 0.0120 reservation (``budget_reservation_overrun``). The ledger still booked the real cost; the
guard now also holds enough for it up front.
"""

from __future__ import annotations

import dataclasses

from mde_contracts.gateway import Usage, WebSearchTool
from mde_core.clock import FakeClock
from mde_llm_gateway.ledger import MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers import ScriptedProvider, ScriptedResponse
from mde_llm_gateway.testing_specs import SEARCH_SPEC, SearchIn, build_test_gateway, make_ctx

ONE_CALL_SPEC = dataclasses.replace(
    SEARCH_SPEC, tools=(WebSearchTool(max_tool_calls=1, search_context_size="low"),)
)


async def test_the_reservation_holds_twice_the_tool_cap_and_a_double_billed_call_fits(
    clock: FakeClock,
) -> None:
    fee = Pricing.load().tool_price("web_search").per_call_micros
    provider = ScriptedProvider(mode="live", billable=True, clock=clock).queue(
        "T4.search",
        ScriptedResponse(
            output="answer text",
            usage=Usage(input_tokens=9_000, output_tokens=170, tool_calls=2),
            search_calls=2,
        ),
    )
    ledger = MemoryLedger(clock=clock)
    gw = build_test_gateway(provider, clock=clock, ledger=ledger)
    await gw.run(ONE_CALL_SPEC, SearchIn(query="q"), make_ctx())
    reserve, commit = ledger.events()[:2]
    assert (reserve.worst_case_micros or 0) >= 2 * fee  # before D-082 it was one fee plus tokens
    assert (commit.cost_micros or 0) >= 2 * fee  # the double-billed call really cost two fees
    assert (commit.cost_micros or 0) <= (reserve.worst_case_micros or 0)  # no overrun any more
    assert gw.budget_status().reserved_micros == 0
