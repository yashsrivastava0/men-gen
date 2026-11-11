"""Research's live spend: read from the shared gateway ledger, and search cost booked into it.

The repository-wide ledger (``data/.budget/ledger.jsonl``) stays authoritative: every model call reserves and
commits there under its spec id (``rsch.*`` for Research). Search calls are not model calls, so they are booked
through the in-process guard (``rsch.search.<provider>``), which makes the global cap and the per-run scope see them.
``research_spent_micros`` adds up what Research has spent so far (read-only, never writes, never prints a key) so the
configurable Research budget (``RESEARCH_LIVE_BUDGET_USD``) is enforced across processes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mde_research_agent.config import RUN_SCOPE_KIND
from mde_research_agent.ids import Ids

SPEC_PREFIX = "rsch."


def research_spent_micros(ledger: Path | str | None, *, prefix: str = SPEC_PREFIX) -> int:
    """Committed micro-dollars of every ledger reservation whose ``spec_id`` starts with ``prefix``."""
    if ledger is None:
        return 0
    path = Path(ledger)
    if not path.is_file():
        return 0
    spec_by_reservation: dict[str, str] = {}
    total = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            rid = str(event.get("reservation_id", ""))
            spec = event.get("spec_id")
            if isinstance(spec, str) and spec:
                spec_by_reservation[rid] = spec
            if event.get("event") != "commit":
                continue
            cost = event.get("cost_micros")
            if (
                isinstance(cost, int)
                and cost >= 0
                and spec_by_reservation.get(rid, "").startswith(prefix)
            ):
                total += cost
    return total


class GuardSpendBook:
    """Books a search call's cost into the shared guard (reserve, then commit the real cost)."""

    def __init__(self, guard: Any, run_id: str, ids: Ids) -> None:
        self._guard = guard
        self._run_id = run_id
        self._ids = ids

    def __call__(self, micros: int, provider: str) -> None:
        res = self._guard.reserve(
            (f"{RUN_SCOPE_KIND}:{self._run_id}",),
            micros,
            purpose="research.search",
            call_id=self._ids.new("call"),
            spec_id=f"{SPEC_PREFIX}search.{provider}",
            model=provider,
            mode="live",
        )
        self._guard.commit_cost(res, micros)
