"""Append-only budget ledger (brief 7.9).

One JSON object per line; three events per reservation lifecycle::

    {"event":"reserve","ts":...,"reservation_id":"rsv_..","scopes":[..],"call_id":..,"spec_id":..,
     "model":..,"mode":"live","worst_case_micros":N}
    {"event":"commit", ...same ids..., "usage":{...},"cost_micros":N}     # real cost
    {"event":"release",...same ids...}                                   # nothing was spent

Totals are never stored: they are *derived* by folding the file (:class:`LedgerState`). The file is
only ever appended to, always under :class:`mde_core.filelock.FileLock`, so any number of processes
(subagents, the lead, tests) share one truth. ``reserve`` checks the caps and appends in ONE critical
section, which is what makes the cap hold under concurrency.

Rules of the fold:

* ``commit`` adds its cost to the global total and to every scope of the reservation; it also closes
  the reservation. A commit for an expired or unknown reservation still counts (money was spent).
* ``release`` closes a reservation without spending.
* A reservation that was never closed and is older than ``reservation_ttl`` counts as SPENT at its
  worst case (``stale_micros``), never as released: the process that held it crashed, or its commit
  failed, and the request may have been billed. A crashed process therefore burns its reservation,
  which is deliberately conservative (the budget can only be freed by an explicit ``release``, or by
  a late ``commit`` that replaces the worst case with the real cost).
* A malformed line is skipped and counted in ``corrupt_lines`` (it cannot be priced).

Guard file (``<ledger>.guard``): written when the ledger is first created and refreshed after every
append with the ledger's size and recorded spend (both only ever grow). A billing gateway refuses to
reserve, and a live provider refuses to start, when the guard exists but the ledger is missing,
smaller than the recorded size or records less spend: deleting or truncating the file must not hand
out a fresh USD 6. The operator resets explicitly (``budget_cli init --force`` or
``MDE_BUDGET_LEDGER_RESET=1``).

Only live (and recorded-live) calls use :class:`FileLedger`; mock/replay/scripted use
:class:`MemoryLedger` (cost 0) so tests never pollute ``data/.budget``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, ValidationError

from mde_contracts.common import Frozen
from mde_contracts.gateway import CallMode, Usage
from mde_core.clock import Clock, SystemClock
from mde_core.errors import LedgerIntegrityError
from mde_core.filelock import FileLock
from mde_core.logging import get_logger

DEFAULT_RESERVATION_TTL_SECONDS = 600
GUARD_SUFFIX = ".guard"

log = get_logger("mde.llm_gateway.ledger")

_RESET_HINT = (
    ". If you have reconciled the spend with the provider dashboard, reset on purpose with "
    "`python -m mde_llm_gateway.budget_cli init --force` (or MDE_BUDGET_LEDGER_RESET=1)"
)

EventKind = Literal["reserve", "commit", "release"]


class LedgerEvent(Frozen):
    """One ledger line. ``extra="forbid"`` (via ``Frozen``) keeps the format closed."""

    event: EventKind
    ts: AwareDatetime
    reservation_id: str
    scopes: list[str] = []
    call_id: str | None = None
    spec_id: str | None = None
    model: str | None = None
    mode: CallMode | None = None
    worst_case_micros: int | None = None  # reserve
    usage: Usage | None = None  # commit
    cost_micros: int | None = None  # commit


@dataclass(frozen=True)
class LedgerSnapshot:
    """Totals derived from the ledger at one instant."""

    spent_micros: int
    reserved_micros: int
    spent_by_scope: dict[str, int]
    reserved_by_scope: dict[str, int]
    live_calls: int
    open_reservations: int
    corrupt_lines: int
    lines: int
    stale_micros: int = 0  # worst cases of expired reservations (already inside spent_micros)
    stale_reservations: int = 0


@dataclass
class _Open:
    ts: datetime
    scopes: tuple[str, ...]
    worst_case_micros: int


@dataclass
class LedgerState:
    """Fold of the ledger lines. Pure and deterministic."""

    spent_micros: int = 0
    spent_by_scope: dict[str, int] = field(default_factory=dict[str, int])
    live_calls: int = 0
    open: dict[str, _Open] = field(default_factory=dict[str, _Open])
    corrupt_lines: int = 0
    lines: int = 0

    def apply_line(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        self.lines += 1
        try:
            event = LedgerEvent.model_validate_json(raw)
        except ValidationError:
            self.corrupt_lines += 1
            return
        self.apply(event)

    def apply(self, event: LedgerEvent) -> None:
        if event.event == "reserve":
            worst = event.worst_case_micros
            if worst is None or worst < 0 or not event.scopes:
                self.corrupt_lines += 1
                return
            self.open[event.reservation_id] = _Open(event.ts, tuple(event.scopes), worst)
        elif event.event == "commit":
            cost = event.cost_micros
            if cost is None or cost < 0:
                self.corrupt_lines += 1
                return
            held = self.open.pop(event.reservation_id, None)
            scopes = tuple(event.scopes) or (held.scopes if held else ())
            self.spent_micros += cost
            for scope in dict.fromkeys(scopes):
                self.spent_by_scope[scope] = self.spent_by_scope.get(scope, 0) + cost
            if event.mode == "live":
                self.live_calls += 1
        else:  # release
            self.open.pop(event.reservation_id, None)

    def snapshot(self, now: datetime, ttl: timedelta) -> LedgerSnapshot:
        reserved = 0
        by_scope: dict[str, int] = {}
        live_open = 0
        stale = 0
        stale_count = 0
        spent = self.spent_micros
        spent_by_scope = dict(self.spent_by_scope)
        for held in self.open.values():
            if now - held.ts > ttl:
                # Stale: the holder crashed or its commit failed. Count the worst case as SPENT.
                stale += held.worst_case_micros
                stale_count += 1
                spent += held.worst_case_micros
                for scope in dict.fromkeys(held.scopes):
                    spent_by_scope[scope] = spent_by_scope.get(scope, 0) + held.worst_case_micros
                continue
            live_open += 1
            reserved += held.worst_case_micros
            for scope in dict.fromkeys(held.scopes):
                by_scope[scope] = by_scope.get(scope, 0) + held.worst_case_micros
        return LedgerSnapshot(
            spent_micros=spent,
            reserved_micros=reserved,
            spent_by_scope=spent_by_scope,
            reserved_by_scope=by_scope,
            live_calls=self.live_calls,
            open_reservations=live_open,
            corrupt_lines=self.corrupt_lines,
            lines=self.lines,
            stale_micros=stale,
            stale_reservations=stale_count,
        )


CapCheck = Callable[[LedgerSnapshot], None]


class Ledger(ABC):
    """Shared logic; subclasses provide storage and the critical section."""

    def __init__(self, *, clock: Clock | None = None, reservation_ttl: float | None = None) -> None:
        self.clock: Clock = clock or SystemClock()
        ttl = DEFAULT_RESERVATION_TTL_SECONDS if reservation_ttl is None else reservation_ttl
        self.reservation_ttl = timedelta(seconds=ttl)
        self._state = LedgerState()
        self._thread_lock = threading.RLock()

    # -- storage hooks
    @abstractmethod
    def _critical(self) -> AbstractContextManager[object]: ...

    @abstractmethod
    def _refresh(self) -> None:
        """Fold every line appended since the last refresh into ``self._state``."""

    @abstractmethod
    def _write(self, event: LedgerEvent) -> None: ...

    def _verify(self) -> None:  # noqa: B027 - optional hook, a no-op by default
        """Refuse (raise) when the storage looks tampered with. Called inside the critical section
        right after a refresh, before every reserve."""

    def _after_write(self) -> None:  # noqa: B027 - optional hook, a no-op by default
        """Called inside the critical section after a line was written and folded in."""

    # -- public API
    def _snapshot(self) -> LedgerSnapshot:
        return self._state.snapshot(self.clock.now(), self.reservation_ttl)

    def snapshot(self) -> LedgerSnapshot:
        with self._thread_lock, self._critical():
            self._refresh()
            return self._snapshot()

    def reserve(self, event: LedgerEvent, check: CapCheck) -> LedgerSnapshot:
        """Atomically: refresh totals, run ``check`` (raises to refuse), append the reserve line."""
        if event.event != "reserve":
            raise ValueError("reserve() needs a reserve event")
        with self._thread_lock, self._critical():
            self._refresh()
            self._verify()
            check(self._snapshot())
            self._write(event)
            self._refresh()
            self._after_write()
            return self._snapshot()

    def append(self, event: LedgerEvent) -> LedgerSnapshot:
        """Append a commit or release line."""
        if event.event == "reserve":
            raise ValueError("use reserve() for reserve events")
        with self._thread_lock, self._critical():
            self._refresh()
            self._write(event)
            self._refresh()
            self._after_write()
            return self._snapshot()

    def close(self) -> None:  # pragma: no cover - nothing to release by default
        return None


class MemoryLedger(Ledger):
    """In-process ledger for mock/replay/scripted modes (never touches ``data/.budget``)."""

    def __init__(self, *, clock: Clock | None = None, reservation_ttl: float | None = None) -> None:
        super().__init__(clock=clock, reservation_ttl=reservation_ttl)
        self.lines: list[str] = []
        self._cursor = 0

    def _critical(self) -> AbstractContextManager[object]:
        return nullcontext()

    def _refresh(self) -> None:
        while self._cursor < len(self.lines):
            self._state.apply_line(self.lines[self._cursor])
            self._cursor += 1

    def _write(self, event: LedgerEvent) -> None:
        self.lines.append(event.model_dump_json(exclude_none=True))

    def events(self) -> list[LedgerEvent]:
        return [LedgerEvent.model_validate_json(line) for line in self.lines]


@dataclass(frozen=True)
class LedgerGuard:
    """What the guard file remembers: the ledger's size and recorded spend (only ever grow)."""

    size_bytes: int
    spent_micros: int
    created_at: str
    lines: int = 0
    first_line_sha256: str | None = None  # a replaced ledger has a different first line


class FileLedger(Ledger):
    """JSON-lines ledger on disk, safe across processes (and on Windows) via ``FileLock``.

    ``<path>.guard`` records the ledger's size and spend so that a deleted or truncated ledger is
    detected (:class:`mde_core.errors.LedgerIntegrityError`) instead of silently resetting the
    budget; see the module docstring.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Clock | None = None,
        reservation_ttl: float | None = None,
        lock_timeout: float = 30.0,
        guard: bool = True,
    ) -> None:
        super().__init__(clock=clock, reservation_ttl=reservation_ttl)
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.guard_path = self.path.with_name(self.path.name + GUARD_SUFFIX)
        self.guard_enabled = guard
        self.lock_timeout = lock_timeout
        self._offset = 0

    def _critical(self) -> AbstractContextManager[object]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(self.lock_path, timeout=self.lock_timeout)

    # ------------------------------------------------------------------ guard file
    def read_guard(self) -> LedgerGuard | None:
        """The guard file's content; ``None`` when there is none; unreadable raises."""
        try:
            raw = self.guard_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise LedgerIntegrityError(
                f"the budget ledger guard {self.guard_path} cannot be read: {exc.__class__.__name__}"
                + _RESET_HINT
            ) from exc
        try:
            data = json.loads(raw)
            first = data.get("first_line_sha256")
            return LedgerGuard(
                size_bytes=int(data["size_bytes"]),
                spent_micros=int(data["spent_micros"]),
                created_at=str(data.get("created_at", "")),
                lines=int(data.get("lines", 0)),
                first_line_sha256=str(first) if first else None,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LedgerIntegrityError(
                f"the budget ledger guard {self.guard_path} is corrupt" + _RESET_HINT
            ) from exc

    def _first_line_digest(self) -> str | None:
        """SHA-256 of the ledger's first complete line (``None`` while there is none)."""
        try:
            with open(self.path, "rb") as fh:
                head = fh.read(65_536)
        except FileNotFoundError:
            return None
        end = head.find(b"\n")
        return hashlib.sha256(head[:end]).hexdigest() if end >= 0 else None

    def _store_guard(
        self,
        size: int,
        spent: int,
        lines: int,
        first_line_sha256: str | None,
        created_at: str | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "size_bytes": size,
                "spent_micros": spent,
                "lines": lines,
                "first_line_sha256": first_line_sha256,
                "created_at": created_at or self.clock.now().isoformat(),
                "updated_at": self.clock.now().isoformat(),
            }
        )
        self.guard_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.guard_path.with_name(self.guard_path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):  # Windows refuses to replace a file another process has open
            try:
                os.replace(tmp, self.guard_path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))

    def _problem(self, guard: LedgerGuard) -> str | None:
        """Why the ledger contradicts ``guard`` (``None`` when it does not)."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return (
                f"the budget ledger {self.path} is missing, but its guard says it held "
                f"{guard.size_bytes} bytes"
            )
        if size < guard.size_bytes:
            return (
                f"the budget ledger {self.path} holds {size} bytes, but its guard says it held "
                f"{guard.size_bytes} (truncated or replaced)"
            )
        if guard.first_line_sha256 and self._first_line_digest() != guard.first_line_sha256:
            return (
                f"the budget ledger {self.path} does not start with the line its guard remembers "
                "(the file was replaced)"
            )
        if self._state.spent_micros < guard.spent_micros or self._state.lines < guard.lines:
            return (
                f"the budget ledger {self.path} records {self._state.spent_micros} micro-dollars "
                f"spent in {self._state.lines} lines, but its guard says {guard.spent_micros} in "
                f"{guard.lines} (lines were removed)"
            )
        return None

    def _verify(self) -> None:
        if not self.guard_enabled:
            return
        guard = self.read_guard()
        if guard is None:  # first creation, or a ledger older than the guard: adopted after write
            return
        problem = self._problem(guard)
        if problem is not None:
            log.critical("budget_ledger_integrity", problem=problem)
            raise LedgerIntegrityError(
                problem
                + "; refusing to reserve (a fresh budget must not appear by deleting a file)"
                + _RESET_HINT
            )

    def _after_write(self) -> None:
        if not self.guard_enabled:
            return
        try:
            old = self.read_guard()
        except LedgerIntegrityError:
            return  # an unreadable guard keeps refusing until the operator resets it
        size = self.path.stat().st_size
        try:
            if old is None:
                self._store_guard(
                    size, self._state.spent_micros, self._state.lines, self._first_line_digest()
                )
            else:
                self._store_guard(
                    max(size, old.size_bytes),
                    max(self._state.spent_micros, old.spent_micros),
                    max(self._state.lines, old.lines),
                    old.first_line_sha256 or self._first_line_digest(),
                    old.created_at,
                )
        except OSError as exc:
            # The line is already in the ledger: never fail the write over the guard. A guard that
            # lags behind the ledger is still safe (it only ever asks for "at least this much").
            log.warning("budget_guard_update_failed", error_type=type(exc).__name__)

    def integrity_problem(self) -> str | None:
        """Read-only check (no lock, no files created) for reports: the problem text, or ``None``."""
        if not self.guard_enabled:
            return None
        try:
            guard = self.read_guard()
        except LedgerIntegrityError as exc:
            return exc.message
        if guard is None:
            return None
        with self._thread_lock:
            self._refresh()
            return self._problem(guard)

    def verify_integrity(self, *, reset: bool = False) -> None:
        """Raise :class:`LedgerIntegrityError` when the ledger contradicts its guard.

        ``reset=True`` is the operator's explicit decision (``init --force`` or
        ``MDE_BUDGET_LEDGER_RESET=1``): the current ledger, whatever it holds, becomes the truth.
        """
        if reset:
            self.reset_guard()
            return
        problem = self.integrity_problem()  # read-only: no lock, no files created
        if problem is not None:
            raise LedgerIntegrityError(problem if _RESET_HINT in problem else problem + _RESET_HINT)

    def reset_guard(self) -> None:
        """Re-baseline the guard on the ledger as it is now (a missing ledger removes the guard)."""
        with self._thread_lock, self._critical():
            self._refresh()
            try:
                previous = self.read_guard()
            except LedgerIntegrityError:
                previous = None
            log.critical(
                "budget_ledger_guard_reset",
                ledger=str(self.path),
                previous_size_bytes=previous.size_bytes if previous else None,
                previous_spent_micros=previous.spent_micros if previous else None,
            )
            if self.path.exists():
                self._store_guard(
                    self.path.stat().st_size,
                    self._state.spent_micros,
                    self._state.lines,
                    self._first_line_digest(),
                )
            else:
                self.guard_path.unlink(missing_ok=True)

    def peek(self) -> LedgerSnapshot:
        """Read-only totals without taking the lock or creating files (reports, the budget CLI).

        Safe against concurrent writers: a half-written last line has no newline yet and is ignored.
        """
        with self._thread_lock:
            self._refresh()
            return self._snapshot()

    def _refresh(self) -> None:
        """Tail-aware read: only the bytes appended since the last refresh are parsed."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self._state, self._offset = LedgerState(), 0
            return
        if size < self._offset:  # truncated or replaced: re-derive from scratch
            self._state, self._offset = LedgerState(), 0
        if size == self._offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return  # only a partial line so far
        complete = chunk[: end + 1]
        self._offset += len(complete)
        for raw in complete.decode("utf-8", errors="replace").splitlines():
            self._state.apply_line(raw)

    def _write(self, event: LedgerEvent) -> None:
        line = event.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        prefix = b""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > 0:  # a crashed writer may have left a partial line: close it first
            with open(self.path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    prefix = b"\n"
        with open(self.path, "ab") as fh:
            fh.write(prefix + line)
            fh.flush()
            os.fsync(fh.fileno())
