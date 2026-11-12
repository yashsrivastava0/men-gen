"""``spend-record.json``: one entry per live action, written AS the run goes.

The shape (schema ``research.spend.v1``)::

    {"schema": "research.spend.v1",
     "periods": [{"id", "started_at", "ended_at", "ledger", "reason", "closed_total_micros"}],
     "entries": [{"id", "period", "at", "kind", "run_id", "input_id", "label", "status",
                  "by_step": {"search.tavily": 12000, "plan": 3000, ...}, "total_micros",
                  "running_total_micros", "reconciled": true|false|null}]}

``kind`` is run, bake-off, probe, ping or smoke. Money is integer micro-dollars everywhere. A write is an atomic
temp-file rename, so a crash leaves either the old or the new file, and every cost is written when it happens. If a
write fails the run goes on: ``write_failed`` is set (the app shows a banner) and the next successful write
rebuilds the file from memory (the gateway ledger is the second source: ``ledger_run_spend``).
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mde_core.clock import Clock, SystemClock
from mde_research_agent.ids import Ids, UlidIds

SCHEMA = "research.spend.v1"
KINDS = ("run", "bakeoff", "probe", "ping", "smoke")


def ledger_run_spend(ledger: Path | str | None, run_id: str) -> int:
    """Committed micro-dollars the gateway ledger holds for one run (every reservation naming its run scope)."""
    if ledger is None or not Path(ledger).is_file():
        return 0
    scope = f"research_run:{run_id}"
    in_scope: set[str] = set()
    total = 0
    with Path(ledger).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            rid = str(ev.get("reservation_id", ""))
            scopes = ev.get("scopes")
            if isinstance(scopes, list) and scope in scopes:
                in_scope.add(rid)
            if ev.get("event") == "commit" and rid in in_scope:
                cost = ev.get("cost_micros")
                if isinstance(cost, int) and cost > 0:
                    total += cost
    return total


def ledger_total(ledger: Path | str | None) -> int:
    """Every committed micro-dollar in one ledger file (a period ledger holds only Research)."""
    if ledger is None or not Path(ledger).is_file():
        return 0
    total = 0
    with Path(ledger).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("event") == "commit":
                cost = ev.get("cost_micros")
                if isinstance(cost, int) and cost > 0:
                    total += cost
    return total


class SpendRecord:
    def __init__(
        self,
        path: Path | str,
        *,
        ledger_for: Any,
        clock: Clock | None = None,
        ids: Ids | None = None,
    ) -> None:
        """``ledger_for(period_id) -> str`` gives the ledger path (stored relative to the repo) of a period."""
        self.path = Path(path)
        self._clock = clock or SystemClock()
        self._ids = ids or UlidIds()
        self._ledger_for = ledger_for
        self._lock = threading.RLock()
        self.write_failed = False
        self.data: dict[str, Any] = {"schema": SCHEMA, "periods": [], "entries": []}
        self._load()
        if not self.data["periods"]:
            self._open_period("initial")

    # --- persistence ---------------------------------------------------------------------------------
    def _load(self) -> None:
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("schema") == SCHEMA:
                    self.data = loaded
                    self.data.setdefault("periods", [])
                    self.data.setdefault("entries", [])
            except (OSError, ValueError):
                self.write_failed = (
                    True  # unreadable: start again from memory; the ledger stays the truth
                )

    def _save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, ensure_ascii=False, indent=1)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
                self.write_failed = False
            except OSError:
                self.write_failed = True

    # --- periods ---------------------------------------------------------------------------------------
    @property
    def period(self) -> dict[str, Any]:
        return self.data["periods"][-1]  # type: ignore[no-any-return]

    def _open_period(self, reason: str) -> dict[str, Any]:
        pid = self._ids.new("prd")
        period = {
            "id": pid,
            "started_at": self._clock.now().isoformat(),
            "ended_at": None,
            "ledger": self._ledger_for(pid),
            "reason": reason,
            "closed_total_micros": None,
        }
        self.data["periods"].append(period)
        self._save()
        return period

    def restart(self, reason: str = "restart") -> dict[str, Any]:
        """Close the current period (history is kept) and open the next one at 0."""
        with self._lock:
            closing = self.period
            closing["ended_at"] = self._clock.now().isoformat()
            closing["closed_total_micros"] = self.period_total_micros(closing["id"])
            return self._open_period(reason)

    # --- entries ------------------------------------------------------------------------------------------
    def period_total_micros(self, period_id: str | None = None) -> int:
        pid = period_id or self.period["id"]
        return sum(int(e["total_micros"]) for e in self.data["entries"] if e["period"] == pid)

    def start_entry(
        self, kind: str, *, run_id: str | None = None, input_id: str | None = None, label: str = ""
    ) -> str:
        if kind not in KINDS:
            raise ValueError(f"unknown spend kind: {kind!r}")
        with self._lock:
            eid = self._ids.new("spn")
            self.data["entries"].append(
                {
                    "id": eid,
                    "period": self.period["id"],
                    "at": self._clock.now().isoformat(),
                    "kind": kind,
                    "run_id": run_id,
                    "input_id": input_id,
                    "label": label[:120],
                    "status": "open",
                    "by_step": {},
                    "total_micros": 0,
                    "running_total_micros": self.period_total_micros(),
                    "reconciled": None,
                    "meta": {},
                }
            )
            self._save()
            return eid

    def _entry(self, entry_id: str) -> dict[str, Any]:
        for e in reversed(self.data["entries"]):
            if e["id"] == entry_id:
                return e  # type: ignore[no-any-return]
        raise KeyError(entry_id)

    def set_meta(self, entry_id: str, meta: dict[str, Any]) -> None:
        with self._lock:
            self._entry(entry_id)["meta"] = {k: v for k, v in meta.items() if v is not None}
            self._save()

    def update_meta(self, entry_id: str, meta: dict[str, Any]) -> None:
        """Merge ``meta`` into the entry's meta (``set_meta`` replaces it). Used for the credits a run booked."""
        with self._lock:
            entry = self._entry(entry_id)
            entry["meta"] = {
                **(entry.get("meta") or {}),
                **{k: v for k, v in meta.items() if v is not None},
            }
            self._save()

    def add_cost(self, entry_id: str, step: str, micros: int) -> None:
        if micros <= 0:
            return
        with self._lock:
            e = self._entry(entry_id)
            by = defaultdict(int, e["by_step"])
            by[step] += micros
            e["by_step"] = dict(by)
            e["total_micros"] = int(e["total_micros"]) + micros
            e["running_total_micros"] = self.period_total_micros(e["period"])
            self._save()

    def finish_entry(self, entry_id: str, status: str, *, ledger_micros: int | None = None) -> None:
        """Close an entry. With ``ledger_micros`` (what the gateway ledger holds for it) the record is reconciled:
        a difference is corrected towards the ledger (it is what the guard enforces) and flagged."""
        with self._lock:
            e = self._entry(entry_id)
            e["status"] = status
            if ledger_micros is not None:
                same = int(e["total_micros"]) == ledger_micros
                e["reconciled"] = same
                if not same:
                    diff = ledger_micros - int(e["total_micros"])
                    e["by_step"]["reconciliation"] = e["by_step"].get("reconciliation", 0) + diff
                    e["total_micros"] = ledger_micros
                    e["running_total_micros"] = self.period_total_micros(e["period"])
            self._save()

    # --- reading --------------------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.data))

    def entries_for_period(self, period_id: str | None = None) -> list[dict[str, Any]]:
        pid = period_id or self.period["id"]
        return [e for e in self.data["entries"] if e["period"] == pid]

    @staticmethod
    def parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)
