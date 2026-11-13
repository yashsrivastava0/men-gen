"""The pack as the Bench shows it: tree with token sizes, task cards by wave, the size report, every check result, and a
file-level diff of two packs. A "pack" is anything with ``.files`` and ``.manifest`` (and ``.tasks``): the ``Pack`` of
``assemble`` or one read back from a run folder (``views.load``)."""

from __future__ import annotations

import math
from typing import Any

from mde_spec_core.canon import sha256_text

_WAVE_ORDER = [f"W{i}" for i in range(8)]


def _factor(pack: Any) -> float:
    return float(pack.manifest.get("tokenizer_chars_per_token") or 3.0)


def file_tokens(pack: Any) -> dict[str, int]:
    """Tokens per file: from the manifest when it lists the file, else counted with the pack's own factor."""
    info = pack.manifest.get("files", {})
    f = _factor(pack)
    out: dict[str, int] = {}
    for path in sorted(pack.files):
        if path in info:
            out[path] = int(info[path]["tokens"])
        else:  # MANIFEST.json and index.json are written after the manifest is made
            out[path] = math.ceil(len(pack.files[path]) / f)
    return out


def _task_total(t: dict[str, Any]) -> int:
    rt = t.get("read_tokens", 0)
    return int(rt["total"] if isinstance(rt, dict) else rt)


def pack_tree(pack: Any) -> dict[str, Any]:
    budget = int(pack.manifest.get("task_read_budget", 0))
    tokens = file_tokens(pack)
    return {
        "files": [{"path": p, "tokens": n} for p, n in tokens.items()],
        "tasks": [
            {
                "id": t["id"],
                "title": t.get("title", ""),
                "read_tokens": _task_total(t),
                "budget": budget,
            }
            for t in pack.tasks
        ],
        "task_read_budget": budget,
        "total_tokens": sum(tokens.values()),
    }


def tasks_view(pack: Any) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {}
    for t in pack.tasks:
        by.setdefault(t["wave"], []).append(
            {
                "id": t["id"],
                "title": t.get("title", ""),
                "tokens": _task_total(t),
                "app": t.get("app"),
                "module": t.get("module"),
                "depends_on": list(t.get("depends_on", [])),
                "complexity": t.get("complexity", ""),
            }
        )
    order = _WAVE_ORDER + sorted(w for w in by if w not in _WAVE_ORDER)
    return {"waves": [{"wave": w, "tasks": by[w]} for w in order if w in by]}


def budget_view(pack: Any) -> dict[str, Any]:
    """The size report. Totals count the files the manifest lists (the same files the packager's own report counts)."""
    info = pack.manifest.get("files", {})
    tokens = {p: n for p, n in file_tokens(pack).items() if p in info or not info}
    by_kind: dict[str, int] = {}
    by_format: dict[str, int] = {}
    bytes_by_format: dict[str, int] = {}
    for p, n in tokens.items():
        kind = p.split("/")[0] if "/" in p else p
        by_kind[kind] = by_kind.get(kind, 0) + n
        leaf = p.rsplit("/", 1)[-1]
        ext = "." + leaf.rsplit(".", 1)[-1] if "." in leaf else "(none)"
        by_format[ext] = by_format.get(ext, 0) + n
        bytes_by_format[ext] = bytes_by_format.get(ext, 0) + len(pack.files[p].encode("utf-8"))
    budget = int(pack.manifest.get("task_read_budget", 0))
    totals = [
        {"id": t["id"], "title": t.get("title", ""), "tokens": _task_total(t), "budget": budget}
        for t in pack.tasks
    ]
    whole = int(pack.manifest.get("whole_pack_tokens", 0))
    total = sum(tokens.values())
    return {
        "total_tokens": total,
        "tokens_by_kind": dict(sorted(by_kind.items())),
        "tokens_by_format": dict(sorted(by_format.items())),
        "bytes_by_format": dict(sorted(bytes_by_format.items())),
        "tasks": len(totals),
        "task_budget": budget,
        "max_task_read": max((t["tokens"] for t in totals), default=0),
        "tasks_over_budget": [t["id"] for t in totals if t["tokens"] > budget],
        "task_totals": totals,
        "whole_pack_tokens": whole,
        "fits_whole_pack_window": (total <= whole) if whole else None,
        "pack_form": pack.manifest.get("pack_form", ""),
    }


def check_dict(check: Any) -> dict[str, Any]:
    """The ``self-check.json`` shape of a CheckReport, or the dict already read from disk."""
    return check if isinstance(check, dict) else check.as_dict()


def checks_view(check: Any) -> dict[str, Any]:
    d = check_dict(check)
    results = [
        {
            "code": e.get("code", ""),
            "severity": e.get("severity", ""),
            "where": e.get("where", ""),
            "why": e.get("why", ""),
            "fix": e.get("fix", ""),
            "repair": e.get("repair", ""),
        }
        for e in d.get("errors", [])
    ]
    needs = list(d.get("builder_needs", {}).get("needs", []))
    cov = d.get("coverage", {})
    rows: list[str] = []
    cells: dict[str, dict[str, Any]] = {}

    def row(label: str, **cols: list[str]) -> None:
        rows.append(label)
        cells[label] = {k: (", ".join(v) if v else False) for k, v in cols.items()}

    for eid, v in sorted(cov.get("entities", {}).items()):
        row(
            f"Entity {eid}",
            screens=v.get("shown_on", []),
            stories=[],
            server_actions=v.get("via_operations", []),
        )
    for rid, v in sorted(cov.get("roles", {}).items()):
        row(f"Role {rid}", screens=v, stories=[], server_actions=[])
    for fid, v in sorted(cov.get("flows", {}).items()):
        row(f"Journey {fid}", screens=v, stories=[], server_actions=[])
    for fid, v in sorted(cov.get("features", {}).items()):
        row(
            f"Feature {fid}",
            screens=v.get("screens", []),
            stories=v.get("stories", []),
            server_actions=[],
        )
    return {
        "ok": bool(d.get("ok")),
        "partial": bool(d.get("partial")),
        "counts": d.get("counts", {}),
        "results": results,
        "builder_needs": needs,
        "builder_needs_summary": d.get("builder_needs", {}).get("summary", {}),
        "coverage": {
            "rows": rows,
            "columns": ["screens", "stories", "server_actions"],
            "cells": cells,
            "gaps": cov.get("gaps", []),
        },
    }


def diff_packs(a: Any, b: Any) -> dict[str, Any]:
    """File-level diff of two packs by content hash: ``files[{path, status}]``, status added, removed, changed or same
    (``a`` is the pack being looked at, ``b`` the one it is compared with: added means only ``a`` has it)."""
    out = []
    for path in sorted(set(a.files) | set(b.files)):
        if path not in b.files:
            status = "added"
        elif path not in a.files:
            status = "removed"
        elif sha256_text(a.files[path]) != sha256_text(b.files[path]):
            status = "changed"
        else:
            status = "same"
        out.append({"path": path, "status": status})
    counts = {
        s: sum(1 for f in out if f["status"] == s) for s in ("added", "removed", "changed", "same")
    }
    return {
        "files": out,
        "counts": counts,
        "identical": counts["added"] + counts["removed"] + counts["changed"] == 0,
    }
