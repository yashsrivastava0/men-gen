"""The file-backed store: the app's default (PostgreSQL stays the production option, ``RESEARCH_STORE=postgres``).

Layout under the data folder (``RESEARCH_DATA_DIR``)::

    runs/<run_id>/run.json          the run row (atomic temp-file + rename on every change)
    runs/<run_id>/events.jsonl      append-only events, one JSON object per line, contiguous ``seq``
    runs/<run_id>/research.json     the committed, hashed result (written once; immutable)
    runs/<run_id>/evidence.json.gz  the evidence file (written with the result)
    runs/<run_id>/decisions.jsonl   the owner's rejections (an overlay; append-only)

One process owns the folder at a time (an exclusive lock file), so single-active-run, contiguous events and
immutability are enforced in code behind one asyncio lock; the same shared contract suite as the in-memory and
PostgreSQL stores checks all of it. Events are also kept in memory for the runs in use, with the byte offset of
every line unnecessary: ``events_after`` slices a list, so SSE polling is O(new events), not O(file).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from mde_core.errors import Conflict, NotFound
from mde_research_agent.domain.events import Event
from mde_research_agent.store.ports import ACTIVE_STATUSES, Decision, RunActive, RunRow

INTERRUPTED_REASON = "server restarted"
KEEP_EVENT_LISTS = 12  # finished runs whose events stay cached in memory


class StoreLocked(Conflict):
    """Another process already owns this data folder."""


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class FileStore:
    def __init__(self, root: Path | str, *, lock: bool = True) -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fh: Any = None
        if lock:
            self._acquire()
        self._guard = asyncio.Lock()
        self._cond = asyncio.Condition()
        self._rows: dict[str, RunRow] = {}
        self._events: OrderedDict[str, list[Event]] = OrderedDict()
        self._decisions: dict[str, list[Decision]] = {}
        self._has_result: set[str] = set()
        self._load()

    # --- startup -----------------------------------------------------------------------------------------
    def _acquire(self) -> None:
        path = self.root / ".store.lock"
        fh = path.open("a+")
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # pragma: no cover - Windows: no advisory lock available here
            pass
        except OSError as exc:
            fh.close()
            raise StoreLocked("another Research process is already using this data folder") from exc
        self._lock_fh = fh

    def _load(self) -> None:
        for d in sorted(self.runs_dir.iterdir()):
            f = d / "run.json"
            if not f.is_file():
                continue
            try:
                row = RunRow.model_validate_json(f.read_text(encoding="utf-8"))
            except ValueError:
                continue  # a torn row from a crash: the atomic rename makes this unreachable, be safe anyway
            self._rows[row.run_id] = row
            if (d / "research.json").is_file():
                self._has_result.add(row.run_id)

    def _dir(self, run_id: str) -> Path:
        d = self.runs_dir / run_id
        if d.parent != self.runs_dir:
            raise NotFound(f"run {run_id}")
        return d

    def _write_row(self, row: RunRow) -> None:
        d = self._dir(row.run_id)
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / "run.json", row.model_dump_json().encode("utf-8"))

    # --- runs ---------------------------------------------------------------------------------------------
    def _active_for(
        self, tenant_id: str, program_id: str, *, skip: str | None = None
    ) -> RunRow | None:
        found = [
            r
            for r in self._rows.values()
            if r.tenant_id == tenant_id
            and r.program_id == program_id
            and r.status in ACTIVE_STATUSES
            and r.run_id != skip
        ]
        found.sort(key=lambda r: (r.created_at, r.run_id), reverse=True)
        return found[0] if found else None

    async def create_run(self, row: RunRow) -> None:
        async with self._guard:
            other = self._active_for(row.tenant_id, row.program_id)
            if other is not None and row.status in ACTIVE_STATUSES:
                raise RunActive(other.run_id)
            self._rows[row.run_id] = row.model_copy(deep=True)
            self._write_row(self._rows[row.run_id])
            self._events.setdefault(row.run_id, [])

    async def update_run(self, tenant_id: str, run_id: str, **fields: Any) -> None:
        async with self._guard:
            row = self._rows.get(run_id)
            if row is None or row.tenant_id != tenant_id:
                raise NotFound(f"run {run_id}")
            new = row.model_copy(update=fields)
            if new.status in ACTIVE_STATUSES and row.status not in ACTIVE_STATUSES:
                other = self._active_for(tenant_id, row.program_id, skip=run_id)
                if other is not None:
                    raise RunActive(other.run_id)
            self._rows[run_id] = new
            self._write_row(new)

    async def get_run(self, tenant_id: str, run_id: str) -> RunRow | None:
        row = self._rows.get(run_id)
        return row.model_copy(deep=True) if row is not None and row.tenant_id == tenant_id else None

    async def active_run(self, tenant_id: str, program_id: str) -> RunRow | None:
        r = self._active_for(tenant_id, program_id)
        return r.model_copy(deep=True) if r is not None else None

    async def latest_committed(self, tenant_id: str, program_id: str) -> RunRow | None:
        rows = [
            r
            for r in self._rows.values()
            if r.tenant_id == tenant_id
            and r.program_id == program_id
            and r.run_id in self._has_result
        ]
        rows.sort(key=lambda x: (x.finished_at or x.created_at, x.run_id), reverse=True)
        return rows[0].model_copy(deep=True) if rows else None

    async def list_runs(self, tenant_id: str, limit: int = 200) -> list[RunRow]:
        rows = [r for r in self._rows.values() if r.tenant_id == tenant_id]
        rows.sort(key=lambda x: (x.created_at, x.run_id), reverse=True)
        return [r.model_copy(deep=True) for r in rows[:limit]]

    async def active_runs(self) -> list[RunRow]:
        rows = [r for r in self._rows.values() if r.status in ACTIVE_STATUSES]
        rows.sort(key=lambda x: (x.created_at, x.run_id))
        return [r.model_copy(deep=True) for r in rows]

    async def mark_interrupted(self) -> int:
        n = 0
        async with self._guard:
            for rid, r in list(self._rows.items()):
                if r.status in ACTIVE_STATUSES:
                    new = r.model_copy(
                        update={"status": "interrupted", "reason": INTERRUPTED_REASON}
                    )
                    self._rows[rid] = new
                    self._write_row(new)
                    n += 1
        return n

    # --- events ---------------------------------------------------------------------------------------------
    def _events_of(self, run_id: str) -> list[Event]:
        cached = self._events.get(run_id)
        if cached is not None:
            self._events.move_to_end(run_id)
            return cached
        out: list[Event] = []
        f = self._dir(run_id) / "events.jsonl"
        if f.is_file():
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(Event.model_validate_json(line))
                    except ValueError:
                        break  # a half-written last line after a crash: ignore it
        self._events[run_id] = out
        self._trim_cache()
        return out

    def _trim_cache(self) -> None:
        idle = [
            rid
            for rid in self._events
            if self._rows.get(rid) is None or self._rows[rid].status not in ACTIVE_STATUSES
        ]
        while len(idle) > KEEP_EVENT_LISTS:
            self._events.pop(idle.pop(0), None)

    async def append_event(self, event: Event) -> None:
        async with self._cond:
            if event.run_id not in self._rows:
                raise NotFound(f"run {event.run_id}")
            log = self._events_of(event.run_id)
            if log and event.seq != log[-1].seq + 1:
                raise ValueError(f"events must be contiguous: got {event.seq} after {log[-1].seq}")
            if not log and event.seq != 1:
                raise ValueError(f"the first event must have seq 1, got {event.seq}")
            line = event.model_dump_json(by_alias=True)
            d = self._dir(event.run_id)
            d.mkdir(parents=True, exist_ok=True)
            with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
            log.append(event)
            self._cond.notify_all()

    async def events_after(self, run_id: str, seq: int, limit: int = 500) -> list[Event]:
        log = self._events_of(run_id) if run_id in self._rows else []
        # seq is contiguous from 1, so the first wanted event is at index ``seq``
        return log[seq : seq + limit] if seq >= 0 else log[:limit]

    async def last_seq(self, run_id: str) -> int:
        log = self._events_of(run_id) if run_id in self._rows else []
        return log[-1].seq if log else 0

    async def wait_for_events(self, run_id: str, after_seq: int, timeout: float) -> None:  # noqa: ASYNC109
        async def _wait() -> None:
            async with self._cond:
                await self._cond.wait_for(
                    lambda: (
                        bool(self._events.get(run_id)) and self._events[run_id][-1].seq > after_seq
                    )
                )

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wait(), timeout=timeout)

    # --- results ----------------------------------------------------------------------------------------------
    async def save_result(
        self, run_id: str, research: dict[str, Any], evidence: dict[str, Any], content_hash: str
    ) -> None:
        async with self._guard:
            row = self._rows.get(run_id)
            if row is None:
                raise NotFound(f"run {run_id}")
            if run_id in self._has_result:
                raise ValueError("a committed result is immutable")
            d = self._dir(run_id)
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(
                d / "evidence.json.gz",
                gzip.compress(json.dumps(evidence, ensure_ascii=False).encode("utf-8"), 5),
            )
            _atomic_write(
                d / "research.json",
                (json.dumps(research, ensure_ascii=False, indent=1) + "\n").encode("utf-8"),
            )
            self._has_result.add(run_id)
            new = row.model_copy(update={"content_hash": content_hash})
            self._rows[run_id] = new
            self._write_row(new)

    async def get_result(self, run_id: str) -> dict[str, Any] | None:
        if run_id not in self._has_result:
            return None
        data = json.loads((self._dir(run_id) / "research.json").read_text(encoding="utf-8"))
        return data  # type: ignore[no-any-return]

    async def get_evidence(self, run_id: str) -> dict[str, Any] | None:
        f = self._dir(run_id) / "evidence.json.gz"
        if run_id not in self._has_result or not f.is_file():
            return None
        return json.loads(gzip.decompress(f.read_bytes()).decode("utf-8"))  # type: ignore[no-any-return]

    # --- decisions ----------------------------------------------------------------------------------------------
    async def add_decision(self, decision: Decision) -> None:
        async with self._guard:
            d = self._dir(decision.run_id)
            d.mkdir(parents=True, exist_ok=True)
            with (d / "decisions.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(decision.model_dump_json() + "\n")
                fh.flush()
            if decision.run_id in self._decisions:
                self._decisions[decision.run_id].append(decision)
            else:  # first use: the file already holds this decision
                self._decisions[decision.run_id] = self._read_decisions(decision.run_id)

    def _read_decisions(self, run_id: str) -> list[Decision]:
        f = self._dir(run_id) / "decisions.jsonl"
        out: list[Decision] = []
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(Decision.model_validate_json(line))
        return out

    async def list_decisions(self, run_id: str) -> list[Decision]:
        if run_id not in self._decisions:
            self._decisions[run_id] = self._read_decisions(run_id) if run_id in self._rows else []
        return list(self._decisions[run_id])

    async def aclose(self) -> None:
        if self._lock_fh is not None:
            with contextlib.suppress(Exception):
                self._lock_fh.close()
            self._lock_fh = None
