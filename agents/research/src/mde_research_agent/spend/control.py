"""The budget control: USD 5.00 in total, a tripwire at 4.50, a hard stop at 5.00, restartable, never exceeded.

* Tripwire: at or above it the run in progress finishes and no new run (or probe) starts.
* Hard stop: every reservation carries the period scope ``research_period:<id>`` capped at the total, so the gateway
  guard refuses, atomically, any call that would pass it (the run then commits ``partial`` with ``budget_stop``).
* Restart: closes the period (history is kept in ``spend-record.json``) and opens a new one with its OWN ledger
  file, so the shared ledger (the archive of everything spent before) is never touched, moved or reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mde_research_agent.config import PERIOD_SCOPE_KIND
from mde_research_agent.spend.record import SpendRecord, ledger_total


@dataclass(frozen=True)
class BudgetState:
    period_id: str
    total_micros: int
    tripwire_micros: int
    spent_micros: int
    state: str  # ok | tripwire | exhausted

    @property
    def remaining_micros(self) -> int:
        return max(0, self.total_micros - self.spent_micros)

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_id": self.period_id,
            "total_micros": self.total_micros,
            "tripwire_micros": self.tripwire_micros,
            "spent_micros": self.spent_micros,
            "remaining_micros": self.remaining_micros,
            "state": self.state,
        }


class BudgetControl:
    def __init__(
        self,
        record: SpendRecord,
        *,
        total_micros: int,
        tripwire_micros: int,
        repo_root: Path,
    ) -> None:
        self.record = record
        self.total_micros = total_micros
        self.tripwire_micros = min(tripwire_micros, total_micros)
        self.repo_root = repo_root

    # --- the period ---------------------------------------------------------------------------------------
    @property
    def period_id(self) -> str:
        return str(self.record.period["id"])

    @property
    def period_scope(self) -> str:
        return f"{PERIOD_SCOPE_KIND}:{self.period_id}"

    @property
    def ledger_path(self) -> Path:
        p = Path(str(self.record.period["ledger"]))
        return p if p.is_absolute() else self.repo_root / p

    @staticmethod
    def ledger_rel(repo_root: Path, period_id: str) -> str:
        return f"data/.budget/research/ledger-{period_id}.jsonl"

    # --- state ------------------------------------------------------------------------------------------------
    def spent_micros(self) -> int:
        """What this period has spent: the larger of the record and the ledger (neither may under-count)."""
        return max(self.record.period_total_micros(), ledger_total(self.ledger_path))

    def state(self) -> BudgetState:
        spent = self.spent_micros()
        label = "ok"
        if spent >= self.total_micros:
            label = "exhausted"
        elif spent >= self.tripwire_micros:
            label = "tripwire"
        return BudgetState(
            self.period_id, self.total_micros, self.tripwire_micros, spent, label
        )

    def can_start(self) -> tuple[bool, str]:
        s = self.state()
        if s.state == "ok":
            return True, ""
        if s.state == "tripwire":
            return False, "budget_tripwire"
        return False, "budget_exhausted"

    def restart(self) -> dict[str, Any]:
        return self.record.restart("restart")
