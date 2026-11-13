"""The Spec spend record: ``agents/spec/.runtime/spend-record.json`` (doc 19 5).

* Integer micro-USD only. A cross-process file lock (``mde_core.FileLock``) guards every read-modify-write, so two
  processes cannot both spend the last dollar.
* ``reserve`` is atomic: if ``spent + reserved + this call > cap`` the call is REFUSED before it leaves.
* ``commit`` books the actual cost; a call that failed after the request left books the WORST CASE (fail closed);
  ``release`` is only for a call that never left.
* A reservation left behind by a dead process counts as SPENT at its worst case (``recover_orphans``), never as free.
* The tripwire (default 4.50): at or above it no new wave starts; calls in flight finish.
* Only the owner restarts a period (``restart``); nothing in the run path does.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mde_core.clock import Clock, SystemClock
from mde_core.filelock import FileLock
from mde_core.ids import new_ulid

SCHEMA = "spec.spend.v1"


class SpendRefused(RuntimeError):
    """The cap (or the tripwire for a new wave) would be passed; the step checkpoints and the run ends partial."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason  # budget_cap | budget_tripwire


@dataclass(frozen=True)
class Reservation:
    id: str
    micros: int
    label: str
    run_id: str


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class SpendRecord:
    def __init__(
        self,
        path: Path | str,
        *,
        cap_micros: int,
        tripwire_micros: int,
        clock: Clock | None = None,
        lock_timeout: float = 30.0,
    ) -> None:
        if not 0 < tripwire_micros <= cap_micros:
            raise ValueError("tripwire must be positive and not above the cap")
        self.path = Path(path)
        self.cap_micros = cap_micros
        self.tripwire_micros = tripwire_micros
        self.clock = clock or SystemClock()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_timeout = lock_timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- file access (always under the lock) ----------------------------------------------------------------
    def _lock(self) -> FileLock:
        return FileLock(self._lock_path, timeout=self._lock_timeout)

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "period": {
                "id": f"per_{new_ulid()}",
                "started_at": _iso(self.clock.now()),
                "cap_micros": self.cap_micros,
                "tripwire_micros": self.tripwire_micros,
            },
            "spent_micros": 0,
            "reservations": {},
            "entries": [],
            "history": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            # A corrupt record must never read as "nothing spent": refuse until the owner looks at it.
            raise SpendRefused(
                "record_corrupt", "the spend record is unreadable; the owner must check it"
            ) from None
        if data.get("schema") != SCHEMA:
            raise SpendRefused("record_schema", "the spend record has an unknown schema")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".spend-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _reserved(data: dict[str, Any]) -> int:
        return sum(int(r["micros"]) for r in data["reservations"].values())

    # ---- operations ----------------------------------------------------------------------------------------
    def reserve(
        self, micros: int, *, label: str, run_id: str = "", pid: int | None = None
    ) -> Reservation:
        if micros < 0:
            raise ValueError("a reservation must be >= 0")
        with self._lock():
            data = self._load()
            spent = int(data["spent_micros"])
            reserved = self._reserved(data)
            if spent + reserved + micros > self.cap_micros:
                raise SpendRefused(
                    "budget_cap",
                    f"this call could cost up to {micros} micro-USD but only "
                    f"{max(0, self.cap_micros - spent - reserved)} remain of the cap",
                )
            res = Reservation(f"rsv_{new_ulid()}", micros, label, run_id)
            data["reservations"][res.id] = {
                "micros": micros,
                "label": label,
                "run_id": run_id,
                "pid": pid if pid is not None else os.getpid(),
                "at": _iso(self.clock.now()),
            }
            self._save(data)
            return res

    def commit(self, res: Reservation, actual_micros: int, *, note: str = "") -> None:
        """Book the real cost. If it exceeds the reservation (it must not) the real figure is still booked."""
        if actual_micros < 0:
            raise ValueError("actual cost must be >= 0")
        with self._lock():
            data = self._load()
            held = data["reservations"].pop(res.id, None)
            data["spent_micros"] = int(data["spent_micros"]) + actual_micros
            data["entries"].append(
                {
                    "label": res.label,
                    "run_id": res.run_id,
                    "reserved_micros": res.micros,
                    "actual_micros": actual_micros,
                    "over_reservation": actual_micros > res.micros,
                    "unknown_reservation": held is None,
                    "note": note,
                    "at": _iso(self.clock.now()),
                }
            )
            self._save(data)

    def book_worst_case(self, res: Reservation, *, note: str = "worst case booked") -> None:
        """Fail closed: a call that failed or timed out after the request left costs its worst case."""
        self.commit(res, res.micros, note=note)

    def release(self, res: Reservation) -> None:
        """Only for a call that never left this process."""
        with self._lock():
            data = self._load()
            data["reservations"].pop(res.id, None)
            self._save(data)

    def recover_orphans(self, *, is_alive: Any) -> int:
        """Reservations of dead processes are booked as spent at worst case. Returns how many."""
        n = 0
        with self._lock():
            data = self._load()
            for rid, r in list(data["reservations"].items()):
                if not is_alive(int(r.get("pid", -1))):
                    data["reservations"].pop(rid)
                    data["spent_micros"] = int(data["spent_micros"]) + int(r["micros"])
                    data["entries"].append(
                        {
                            "label": r["label"],
                            "run_id": r["run_id"],
                            "reserved_micros": int(r["micros"]),
                            "actual_micros": int(r["micros"]),
                            "note": "orphan reservation booked at worst case",
                            "at": _iso(self.clock.now()),
                        }
                    )
                    n += 1
            if n:
                self._save(data)
        return n

    # ---- reading ------------------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock():
            data = self._load()
        spent = int(data["spent_micros"])
        reserved = self._reserved(data)
        return {
            "period_id": data["period"]["id"],
            "cap_micros": self.cap_micros,
            "tripwire_micros": self.tripwire_micros,
            "spent_micros": spent,
            "reserved_micros": reserved,
            "remaining_micros": max(0, self.cap_micros - spent - reserved),
            "state": self._state(spent, reserved),
            "entries": len(data["entries"]),
        }

    def _state(self, spent: int, reserved: int) -> str:
        if spent + reserved >= self.cap_micros:
            return "exhausted"
        if spent >= self.tripwire_micros:
            return "tripwire"
        return "ok"

    def spent_micros(self) -> int:
        return int(self.snapshot()["spent_micros"])

    def remaining_micros(self) -> int:
        return int(self.snapshot()["remaining_micros"])

    def can_start_wave(self) -> None:
        """Raise SpendRefused at or above the tripwire (no new wave; calls in flight finish)."""
        s = self.snapshot()
        if s["state"] == "exhausted":
            raise SpendRefused("budget_cap", "the budget cap is reached")
        if s["state"] == "tripwire":
            raise SpendRefused("budget_tripwire", "the tripwire is reached: no new wave starts")

    def restart(self, reason: str) -> dict[str, Any]:
        """Owner only. Closes the period (history kept) and opens a new one."""
        with self._lock():
            data = self._load() if self.path.is_file() else self._empty()
            closed = {
                **data["period"],
                "closed_at": _iso(self.clock.now()),
                "reason": reason,
                "spent_micros": data["spent_micros"],
            }
            fresh = self._empty()
            fresh["history"] = [*data.get("history", []), closed]
            self._save(fresh)
            return fresh["period"]
