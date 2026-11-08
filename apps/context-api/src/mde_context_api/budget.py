"""The budget gate: the flow's ``BudgetGate`` port over the LLM gateway's budget status.

Spec 9, "Enterprise: budget cap reached": pause before the Plan phase, explain, ask an approver.
The flow asks ``check`` before it starts a plan run and before each plan phase; this module answers
from what the gateway knows, so the flow never imports the gateway.

``ok`` is False when planning could not be done properly with what is left:

* the ledger's tripwire is at 80% or 95% (``must_run_only_80`` / ``refuse_95``: from there the
  gateway refuses every call that is not a must-run one, and planning calls are not),
* the whole budget has less headroom under its effective cap than one plan needs,
* the session's own cap (``session:<id>``) has less headroom than one plan needs.

"One plan" is ``plan_reserve_micros`` (``BUDGET_PLAN_RESERVE_USD``, default USD 0.05): about what
the build phase costs. The verdict carries a plain sentence and never a number. The gateway's own
guard stays the authority: this gate only decides to stop early and ask, instead of letting every
call fall back one by one. A gate that cannot read the status says "ok" (the flow treats an error
the same way) because the guard still refuses a call that would break a cap.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Final

from mde_contracts.gateway import BudgetStatus, GatewayClient
from mde_contracts.ports import BudgetGate, BudgetVerdict
from mde_core.money import usd_to_micros
from mde_flow.messages import BUDGET_REACHED
from mde_llm_gateway import Gateway, InProcessGatewayClient
from mde_llm_gateway.budget import ScopeUsage

DEFAULT_PLAN_RESERVE_MICROS: Final = usd_to_micros("0.05")
TRIPWIRE_MESSAGE: Final = (
    "The shared AI budget is nearly used up, so I paused before building. "
    "Someone who can approve it needs to top it up."
)

StatusFn = Callable[[], Awaitable[BudgetStatus]]
ScopeUsageFn = Callable[[str], ScopeUsage]


class NoopBudgetGate:
    """No budget information (a gateway without status): planning is never paused."""

    async def check(self, *, session_id: str, tenant_id: str) -> BudgetVerdict:
        return BudgetVerdict(ok=True)


class GatewayBudgetGate:
    def __init__(
        self,
        status: StatusFn,
        *,
        scope_usage: ScopeUsageFn | None = None,
        plan_reserve_micros: int = DEFAULT_PLAN_RESERVE_MICROS,
    ) -> None:
        self._status = status
        self._scope_usage = scope_usage
        self._reserve = max(0, plan_reserve_micros)

    @classmethod
    def for_gateway(
        cls, gateway: Gateway, *, plan_reserve_micros: int = DEFAULT_PLAN_RESERVE_MICROS
    ) -> GatewayBudgetGate:
        """The full gate over an in-process gateway: global status and the session's own cap."""

        async def status() -> BudgetStatus:
            return gateway.budget_status()

        return cls(
            status,
            scope_usage=gateway.guard.usage_for_scope,
            plan_reserve_micros=plan_reserve_micros,
        )

    async def check(self, *, session_id: str, tenant_id: str) -> BudgetVerdict:
        status = await self._status()
        if status.tripwire in ("refuse_95", "must_run_only_80"):
            return BudgetVerdict(ok=False, reason="tripwire", message=TRIPWIRE_MESSAGE)
        committed = status.spent_micros + status.reserved_micros
        if committed + self._reserve > status.effective_cap_micros:
            return BudgetVerdict(ok=False, reason="cap_reached", message=BUDGET_REACHED)
        if self._scope_usage is not None:
            usage = self._scope_usage(f"session:{session_id}")
            if usage.cap_micros is not None:
                used = usage.spent_micros + usage.reserved_micros
                if usage.cap_micros - used < self._reserve:
                    return BudgetVerdict(ok=False, reason="cap_reached", message=BUDGET_REACHED)
        return BudgetVerdict(ok=True)


def _status_of(client: GatewayClient) -> StatusFn | None:
    """The client's ``budget_status`` as an async function (it may be sync), or None."""
    fn = getattr(client, "budget_status", None)
    if fn is None:
        return None

    async def status() -> BudgetStatus:
        result = fn()
        if inspect.isawaitable(result):
            result = await result
        return result  # type: ignore[no-any-return]

    return status


def budget_gate_for(
    client: GatewayClient, *, plan_reserve_micros: int = DEFAULT_PLAN_RESERVE_MICROS
) -> BudgetGate:
    """The gate for the composition root's gateway client.

    In-process: global status plus the session's own cap. Another client (HTTP) that has a status:
    the global part only (the service does not tell us a session's cap). No status: no gate."""
    if isinstance(client, InProcessGatewayClient):
        return GatewayBudgetGate.for_gateway(
            client.gateway, plan_reserve_micros=plan_reserve_micros
        )
    status = _status_of(client)
    if status is None:
        return NoopBudgetGate()
    return GatewayBudgetGate(status, plan_reserve_micros=plan_reserve_micros)
