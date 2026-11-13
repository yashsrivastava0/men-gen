"""Rebuild the objects the views need from a run folder (the ONLY place in ``views`` that reads files).

``graph.json`` -> ``SpecGraph``; ``pack/*`` -> a ``Pack``; ``evidence/self-check.json`` -> the check dict; ``run.json``;
the derived plan (inventory, release split, profile decision) is re-derived from the Compass export the run records,
which is deterministic and costs nothing. Everything else in ``views`` is a pure function of these objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mde_compass_intake import intake as read_intake
from mde_compass_intake import read_research
from mde_spec_agent.derive.pipeline import derive_graph
from mde_spec_agent.run.store import NotFound, RunStore
from mde_spec_core.graph import SpecGraph
from mde_spec_core.pack import Pack
from mde_spec_core.simulate import block_bodies

from .packview import budget_view


@dataclass
class LoadedRun:
    run: dict[str, Any]
    graph: SpecGraph
    pack: Pack | None
    check: dict[str, Any] | None
    derived: Any = None
    research: Any = None
    verify: dict[str, Any] | None = None
    flags: list[Any] = field(default_factory=list)
    banners: list[Any] = field(default_factory=list)
    rcode_rows: list[dict[str, Any]] = field(default_factory=list)


def read_pack_folder(pack_dir: Path) -> Pack:
    """A ``Pack`` read back from a folder written by the assemble step."""
    files: dict[str, str] = {}
    for p in sorted(pack_dir.rglob("*")):
        if p.is_file():
            files[p.relative_to(pack_dir).as_posix()] = p.read_text(encoding="utf-8")
    manifest = json.loads(files["MANIFEST.json"])
    index = json.loads(files["index.json"])
    plan = json.loads(files.get("build/build-plan.json", "{}"))
    needs = json.loads(files["global/needs.json"]) if "global/needs.json" in files else []
    pack = Pack(
        files=files,
        manifest=manifest,
        index=index,
        tasks=list(plan.get("tasks", [])),
        budget_report={},
        content_hash=manifest.get("content_hash", ""),
        package_hash=manifest.get("package_hash", ""),
        blocks=block_bodies(files),
        needs=needs,
        pilots={},
    )
    pack.budget_report = budget_view(pack)
    return pack


def load_pack(store: RunStore, run_id: str) -> Pack | None:
    d = store.run_dir(run_id) / "pack"
    return read_pack_folder(d) if (d / "MANIFEST.json").is_file() else None


def _json(store: RunStore, run_id: str, rel: str) -> Any:
    try:
        return json.loads(store.read_text(run_id, rel))
    except (NotFound, ValueError):
        return None


def load_graph(store: RunStore, run_id: str) -> SpecGraph:
    return SpecGraph.model_validate_json(store.read_text(run_id, "graph.json"))


def load_derived(rec: dict[str, Any]) -> tuple[Any, Any]:
    """``(derived, research view)`` re-derived from the run's recorded inputs; ``(None, None)`` for a synthetic or pathless run."""
    path = rec.get("compass_path")
    if rec.get("synth") or not path or not Path(path).is_file():
        return None, None
    got = read_intake(json.loads(Path(path).read_text(encoding="utf-8")))
    if not got.readiness.ready or got.spec_input is None:
        return None, None
    rpath = rec.get("research_path")
    research = (
        read_research(json.loads(Path(rpath).read_text(encoding="utf-8")))
        if rpath and Path(rpath).is_file()
        else None
    )
    g15 = (rec.get("gates") or {}).get("g15") or {}
    one = bool(g15.get("everything_in_one_release")) if isinstance(g15, dict) else False
    return derive_graph(got.spec_input, research, everything_in_one_release=one), research


def load_run(store: RunStore, run_id: str, *, with_derived: bool = True) -> LoadedRun:
    rec = store.get_run(run_id)
    graph = load_graph(store, run_id)
    check = _json(store, run_id, "evidence/self-check.json")
    derived, research = load_derived(rec) if with_derived else (None, None)
    return LoadedRun(
        run=rec,
        graph=graph,
        pack=load_pack(store, run_id),
        check=check,
        derived=derived,
        research=research,
        verify=_json(store, run_id, "evidence/verify.json"),
        flags=list((check or {}).get("flags", [])),
        banners=list((check or {}).get("banners", [])),
        rcode_rows=list(((check or {}).get("rcode") or {}).get("rows", [])),
    )
