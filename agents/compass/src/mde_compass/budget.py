"""The Compass-only live-test budget (prompt 4): additive to the repo's global cap and ledger.

* ``COMPASS_LIVE_BUDGET_USD`` is clamped to <= 2.00 (``config.load_budget_policy``); effective stop at 95%
  of the cap (1.90); per-session cap 0.25; **fails closed**: when a check cannot prove the spend fits, it
  raises ``BudgetExceeded`` and the call runner uses the deterministic fallback instead of calling the model.
* Spend is tagged per session and in total here (a Compass-only counter); the gateway ledger remains the
  source of truth for the global USD 6.00 cap and is reconciled by ``reconcile`` (the larger number wins).
* Money is integer micro-dollars; USD strings in ``snapshot`` are ``Decimal`` text, never float.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from mde_compass.calls.base import SpendGuard
from mde_compass.config import MICROS_PER_USD, BudgetPolicy
from mde_core.errors import BudgetExceeded


def _usd(micros: int) -> str:
    return str((Decimal(micros) / Decimal(MICROS_PER_USD)).quantize(Decimal("0.0001")))


class CompassBudget(SpendGuard):
    def __init__(
        self, policy: BudgetPolicy | None = None, *, mode: Literal["mock", "live"] = "mock"
    ) -> None:
        self.policy = policy or BudgetPolicy()
        self.mode = mode
        self.spent_micros = 0
        self.calls = 0
        self._by_session: dict[str, int] = {}

    def session_spent(self, session_id: str) -> int:
        return self._by_session.get(session_id, 0)

    def check(self, session_id: str, *, estimate_micros: int = 0) -> None:
        """Raise ``BudgetExceeded`` unless total and per-session spend (plus the estimate) stay below the
        effective stop. In mock mode every cost is zero, so this never trips there."""
        if estimate_micros < 0:
            raise BudgetExceeded("negative estimate: refusing (fail closed)")
        total_after = self.spent_micros + estimate_micros
        if total_after >= self.policy.effective_stop_micros:
            raise BudgetExceeded(
                f"compass cap reached: {_usd(self.spent_micros)} of {self.policy.cap_usd} "
                f"(stop at {_usd(self.policy.effective_stop_micros)})"
            )
        if self.session_spent(session_id) + estimate_micros >= self.policy.per_session_micros:
            raise BudgetExceeded(
                f"compass per-session cap reached ({self.policy.per_session_cap_usd})"
            )

    def record(self, session_id: str, cost_micros: int) -> None:
        cost = max(0, int(cost_micros))
        self.spent_micros += cost
        self.calls += 1
        self._by_session[session_id] = self._by_session.get(session_id, 0) + cost

    def reconcile(self, ledger_spent_micros: int) -> None:
        """Never under-report: if the gateway ledger saw more spend, adopt it."""
        if ledger_spent_micros > self.spent_micros:
            self.spent_micros = ledger_spent_micros

    def snapshot(self) -> dict[str, str | int]:
        p = self.policy
        return {
            "mode": self.mode,
            "cap_usd": str(p.cap_usd),
            "hard_cap_usd": str(p.hard_cap_usd),
            "effective_stop_usd": _usd(p.effective_stop_micros),
            "per_session_cap_usd": str(p.per_session_cap_usd),
            "spent_usd": _usd(self.spent_micros),
            "remaining_usd": _usd(max(0, p.effective_stop_micros - self.spent_micros)),
            "spent_micros": self.spent_micros,
            "calls": self.calls,
        }
