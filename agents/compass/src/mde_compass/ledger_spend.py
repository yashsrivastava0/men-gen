"""Compass's live spend, recomputed from the gateway's ledger (read-only).

``CompassBudget`` is an in-process counter, so it resets with every process, which would let a series of live
batches overspend the Compass cap. The gateway ledger (``data/.budget/ledger.jsonl``) is the shared truth: every
call reserves and commits there with its ``spec_id``. This module adds up the committed cost of the calls whose
spec id starts with ``cmp.`` (all of Compass's calls), so each process starts from what was really spent.

Read-only: it never takes the ledger lock, never writes, never creates a file and never prints a key. A missing
ledger is zero. A line it cannot read is skipped (the gateway's own guard is what refuses a damaged ledger).
"""

from __future__ import annotations

import json
from pathlib import Path

SPEC_PREFIX = "cmp."


def compass_spent_micros(ledger: Path | str | None = None, *, prefix: str = SPEC_PREFIX) -> int:
    """Committed micro-dollars of every ledger reservation whose ``spec_id`` starts with ``prefix``."""
    path = _ledger_path(ledger)
    if path is None or not path.is_file():
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
            if not isinstance(cost, int) or cost < 0:
                continue
            if spec_by_reservation.get(rid, "").startswith(prefix):
                total += cost
    return total


def _ledger_path(ledger: Path | str | None) -> Path | None:
    if ledger is not None:
        return Path(ledger)
    from mde_core.settings import load_settings

    return Path(load_settings().llm_budget_ledger)
