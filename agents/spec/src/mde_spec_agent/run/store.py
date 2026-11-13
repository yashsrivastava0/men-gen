"""File-based run store under `agents/spec/.runtime/` (no database). Cross-process safe (one lock file), idempotent
writes, dense event sequence, single-flight per program (doc 15 8.4).

Layout: runs/<run_id>/{run.json, events.jsonl, graph.json, checkpoints/<key>.json, pack/..., evidence/...},
programs/<program>.active (the active run id), inputs/.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mde_core.clock import Clock, SystemClock
from mde_core.filelock import FileLock
from mde_core.ids import new_ulid

from .events import TERMINAL, Event

ACTIVE = {"queued", "running", "paused_g15", "awaiting_g2"}
INTERRUPTED_REASON = "server restarted"


class RunActive(RuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already active for this program ({run_id})")
        self.run_id = run_id


class NotFound(KeyError):
    pass


@dataclass
class Joined:
    run_id: str
    joined: bool


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".w-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class RunStore:
    def __init__(self, root: Path | str, *, clock: Clock | None = None) -> None:
        self.root = Path(root)
        self.clock = clock or SystemClock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".store.lock"

    def _lock(self) -> FileLock:
        return FileLock(self._lock_path, timeout=30.0)

    def _now(self) -> str:
        return self.clock.now().isoformat()

    def run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    # ---- runs ----------------------------------------------------------------------------------------------
    def create_run(self, program_id: str, input_hash: str, fields: dict[str, Any]) -> Joined:
        """Single-flight per program: the same input hash joins the active run; a different hash is refused."""
        with self._lock():
            marker = self.root / "programs" / f"{program_id}.active"
            if marker.is_file():
                active = marker.read_text(encoding="utf-8").strip()
                try:
                    rec = self.get_run(active)
                except NotFound:
                    rec = None
                if rec and rec["status"] in ACTIVE:
                    if rec["input_hash"] == input_hash:
                        return Joined(active, True)
                    raise RunActive(active)
            run_id = f"run_{new_ulid()}"
            rec = {
                "id": run_id,
                "program_id": program_id,
                "input_hash": input_hash,
                "status": "queued",
                "step": None,
                "created_at": self._now(),
                "updated_at": self._now(),
                "gates": {"g15": None, "g2": None},
                "spend_micros": 0,
                "banners": [],
                "checked_by": ["self_check"],
                "critic_vendor": None,
                **fields,
            }
            _atomic_write(
                self.run_dir(run_id) / "run.json", json.dumps(rec, sort_keys=True, indent=1)
            )
            _atomic_write(marker, run_id)
            return Joined(run_id, False)

    def get_run(self, run_id: str) -> dict[str, Any]:
        p = self.run_dir(run_id) / "run.json"
        if not p.is_file():
            raise NotFound(run_id)
        return json.loads(p.read_text(encoding="utf-8"))

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock():
            rec = self.get_run(run_id)
            rec.update(fields)
            rec["updated_at"] = self._now()
            _atomic_write(
                self.run_dir(run_id) / "run.json", json.dumps(rec, sort_keys=True, indent=1)
            )
            if rec["status"] not in ACTIVE:
                marker = self.root / "programs" / f"{rec['program_id']}.active"
                if marker.is_file() and marker.read_text(encoding="utf-8").strip() == run_id:
                    marker.unlink()
            return rec

    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        base = self.root / "runs"
        for d in sorted(base.glob("run_*")) if base.is_dir() else []:
            try:
                out.append(json.loads((d / "run.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    def recover_interrupted(self) -> list[str]:
        """After a restart no run is really running: mark them partial (resumable) with a plain reason."""
        ids = []
        for rec in self.list_runs():
            if rec["status"] in ("queued", "running"):
                self.update_run(
                    rec["id"], status="partial", partial_reason="crash", note=INTERRUPTED_REASON
                )
                self.append_event(
                    rec["id"], "run.partial", data={"reason": "crash", "note": INTERRUPTED_REASON}
                )
                ids.append(rec["id"])
        return ids

    # ---- events --------------------------------------------------------------------------------------------
    def append_event(
        self,
        run_id: str,
        type_: str,
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        with self._lock():
            path = self.run_dir(run_id) / "events.jsonl"
            seq = 1
            if path.is_file():
                with path.open("rb") as fh:
                    for seq_line in fh:
                        seq += 1 if seq_line.strip() else 0
            ev = Event(
                seq=seq, type=type_, at=self._now(), run_id=run_id, step=step, data=data or {}
            )  # type: ignore[arg-type]
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(ev.model_dump_json() + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return ev

    def events(self, run_id: str, after: int = 0) -> list[Event]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ev = Event.model_validate_json(line)
                if ev.seq > after:
                    out.append(ev)
        return out

    def is_terminal(self, run_id: str) -> bool:
        evs = self.events(run_id)
        return bool(evs) and evs[-1].type in TERMINAL

    # ---- checkpoints (idempotent) and artifacts --------------------------------------------------------------
    def put_checkpoint(self, run_id: str, key: str, value: dict[str, Any]) -> None:
        _atomic_write(
            self.run_dir(run_id) / "checkpoints" / f"{key}.json",
            json.dumps(value, sort_keys=True, ensure_ascii=False),
        )

    def get_checkpoint(self, run_id: str, key: str) -> dict[str, Any] | None:
        p = self.run_dir(run_id) / "checkpoints" / f"{key}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def write_text(self, run_id: str, rel: str, text: str) -> None:
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError("bad artifact path")
        _atomic_write(self.run_dir(run_id) / rel, text)

    def read_text(self, run_id: str, rel: str) -> str:
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError("bad artifact path")
        p = self.run_dir(run_id) / rel
        if not p.is_file():
            raise NotFound(rel)
        return p.read_text(encoding="utf-8")
