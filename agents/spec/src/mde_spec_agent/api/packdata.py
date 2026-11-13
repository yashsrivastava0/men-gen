"""The pack of a run, read back from its folder (``runs/<id>/pack`` and ``evidence``): pure reads, no model, no graph.

Everything here answers ``/pack/*``, ``/simulate`` and ``/diff``. A pack holds user and model text; this module only
moves text around and counts it, and the file route serves it as ``text/plain``.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

from mde_spec_agent.run.store import NotFound as StoreNotFound
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.views.load import read_pack_folder
from mde_spec_core import simulate as sim
from mde_spec_core.config import SpecConfig
from mde_spec_core.pack import Pack
from mde_spec_core.render import TokenCounter

PACK_TOP = ("MANIFEST.json", "index.json", "build/build-plan.json")


class PackFolder:
    """One run's pack files, manifest, task cards and evidence."""

    def __init__(self, store: RunStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self.root = store.run_dir(run_id)

    # ---- raw reads ------------------------------------------------------------------------------------------
    def _json(self, rel: str) -> Any | None:
        try:
            return json.loads(self.store.read_text(self.run_id, rel))
        except (StoreNotFound, ValueError):
            return None

    @cached_property
    def manifest(self) -> dict[str, Any]:
        data = self._json("pack/MANIFEST.json")
        return data if isinstance(data, dict) else {}

    @cached_property
    def index(self) -> dict[str, Any]:
        data = self._json("pack/index.json")
        return data if isinstance(data, dict) else {}

    @cached_property
    def build_plan(self) -> dict[str, Any]:
        data = self._json("pack/build/build-plan.json")
        return data if isinstance(data, dict) else {}

    @property
    def exists(self) -> bool:
        return bool(self.manifest)

    @cached_property
    def tasks(self) -> list[dict[str, Any]]:
        return list(self.build_plan.get("tasks", []))

    def task(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tasks if str(t.get("id")) == task_id), None)

    def files(self) -> dict[str, str]:
        base = self.root / "pack"
        out: dict[str, str] = {}
        if not base.is_dir():
            return out
        for path in sorted(base.rglob("*")):
            if path.is_file():
                out[path.relative_to(base).as_posix()] = path.read_text(encoding="utf-8")
        return out

    def as_pack(self) -> SimpleNamespace:
        """What the simulator reads: anything with ``files``, ``manifest`` and ``tasks``."""
        return SimpleNamespace(
            files=self.files(), manifest=self.manifest, tasks=self.tasks, index=self.index
        )

    def counter(self) -> TokenCounter:
        return TokenCounter(float(self.manifest.get("tokenizer_chars_per_token", 3.0)))

    # ---- listings -------------------------------------------------------------------------------------------
    def evidence_names(self) -> list[str]:
        base = self.root / "evidence"
        return sorted(p.name for p in base.glob("*") if p.is_file()) if base.is_dir() else []

    def allowed_paths(self) -> set[str]:
        """Every path the file route may serve: the manifest's files, the pack's top files, and evidence files."""
        out = set((self.manifest.get("files") or {}).keys())
        out.update(p for p in PACK_TOP if (self.root / "pack" / p).is_file())
        out.update(f"evidence/{n}" for n in self.evidence_names())
        return out

    def read_file(self, path: str) -> str | None:
        """The text of one file, or None when ``path`` is not one of the pack's own files (never a traversal)."""
        if not path or "\x00" in path or "\\" in path:
            return None
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or str(pure) != path:
            return None
        if path not in self.allowed_paths():
            return None
        target = self.root / (path if path.startswith("evidence/") else f"pack/{path}")
        try:
            resolved = target.resolve()
            resolved.relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        return resolved.read_text(encoding="utf-8") if resolved.is_file() else None

    # ---- evidence -------------------------------------------------------------------------------------------
    def selfcheck(self) -> dict[str, Any] | None:
        data = self._json("evidence/self-check.json")
        return data if isinstance(data, dict) else None

    def verify(self) -> dict[str, Any] | None:
        data = self._json("evidence/verify.json")
        return data if isinstance(data, dict) else None

    def pack(self) -> Pack:
        """The ``Pack`` object the view functions read (files, manifest, tasks, needs, blocks)."""
        return read_pack_folder(self.root / "pack")


# ---- the simulator ------------------------------------------------------------------------------------------------


def _title(task: dict[str, Any]) -> str:
    return str(task.get("title", task.get("id", "")))


def simulate(
    pack: PackFolder,
    mode: str,
    *,
    consumer: Any,
    screen: str | None,
    cfg: SpecConfig,
    k: int,
) -> dict[str, Any]:
    """The three modes against a consumer; totals come from spec-core's simulator, so they equal the packager's."""
    ns = pack.as_pack()
    if mode == "all":
        r = sim.simulate_all(ns, consumer, cfg)
        r["pack_tokens"] = r["total_tokens"]
        return r
    if mode == "one":
        if not screen:
            return {"found": False, "reason": "Say which screen to read (screen=SCR-001)."}
        r = sim.simulate_one(ns, screen, config=cfg)
        if not r.get("found"):
            return r
        c = sim.counter_for(ns)
        files = [
            {
                "part": "prefix",
                "path": "prefix.bundle.md",
                "bytes": len(ns.files["prefix.bundle.md"].encode("utf-8")),
                "tokens": r["tokens"]["prefix"],
            },
            {
                "part": "card",
                "path": r["files"][1],
                "bytes": len(ns.files[r["files"][1]].encode("utf-8")),
                "tokens": r["tokens"]["screen"],
            },
        ]
        owner = next(
            (
                t
                for t in ns.tasks
                if f"screens/{screen}.md" in (t.get("reads") or {}).get("screens", [])
            ),
            None,
        )
        out = {
            **r,
            "files": files,
            "total_tokens": r["token_total"],
            "budget_tokens": r["budget"],
        }
        if owner is not None:
            tt = sim.task_tokens(ns, owner, c, cfg)
            out["task_id"] = owner["id"]
            out["task_total_tokens"] = tt["total"]
        return out
    r = sim.simulate_parallel(ns, k, consumer, cfg)
    rows = {row["task"]: row for row in r.get("timeline", [])}
    waves = []
    for wave, ids in sorted(r["waves"].items()):
        tasks = []
        for tid in ids:
            card = pack.task(tid) or {}
            row = rows.get(tid, {})
            tasks.append(
                {
                    "id": tid,
                    "title": _title(card),
                    "tokens": row.get("read_tokens", 0),
                    "slot": row.get("slot"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                }
            )
        waves.append({"wave": wave, "tasks": tasks})
    return {**r, "slots": r["k"], "waves": waves, "mode": "parallel"}
