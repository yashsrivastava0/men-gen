"""Plain numbers for one Spec run folder (read-only, no model, no key): calls and cost by kind, first-try pass and repair
rates, R-code counts, banners, validator results, pack and task sizes, spend. Used to fill the report honestly.

    uv run python scripts/spec_run_report.py <run-dir> [--out <dir>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load(p: Path) -> object:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def report(run: Path) -> dict:
    rec = load(run / "run.json") or {}
    events = (
        [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if (run / "events.jsonl").is_file()
        else []
    )
    kinds: dict[str, dict] = defaultdict(
        lambda: {
            "calls": 0,
            "repaired": 0,
            "fallback": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_micros": 0,
        }
    )
    for e in events:
        if e["type"] != "call.finished":
            continue
        d, k = e["data"], kinds[e["data"].get("kind", "?")]
        k["calls"] += 1
        k["repaired"] += 1 if d.get("repaired") else 0
        k["fallback"] += 1 if d.get("fallback") else 0
        k["clean"] = k.get("clean", 0) + (0 if (d.get("repaired") or d.get("fallback")) else 1)
        if d.get("fallback") and d.get("reason"):
            k.setdefault("reasons", []).append(d["reason"][:160])
        k["tokens_in"] += int((d.get("tokens") or {}).get("in", 0))
        k["tokens_out"] += int((d.get("tokens") or {}).get("out", 0))
        k["cost_micros"] += int(d.get("cost_micros", 0))
    for k in kinds.values():
        n = max(1, k["calls"])
        k["first_try_pass"] = round(k.get("clean", 0) / n, 3)
        k["repair_rate"] = round(k["repaired"] / n, 3)
    sc = load(run / "evidence" / "self-check.json") or {}
    man = load(run / "pack" / "MANIFEST.json") or {}
    plan = load(run / "pack" / "build" / "build-plan.json") or {}
    tasks = plan.get("tasks", [])
    reads = [t["read_tokens"]["total"] for t in tasks if "read_tokens" in t]
    total_cost = sum(k["cost_micros"] for k in kinds.values())
    return {
        "run_id": rec.get("id"),
        "status": rec.get("status"),
        "partial_reason": rec.get("partial_reason"),
        "profile": rec.get("profile"),
        "offline": rec.get("offline"),
        "calls_total": sum(k["calls"] for k in kinds.values()),
        "cost_usd": round(total_cost / 1e6, 4),
        "by_kind": dict(sorted(kinds.items())),
        "banners": [e["data"] for e in events if e["type"] == "banner"],
        "rcode_counts": (sc.get("rcode") or {}).get("counts") or sc.get("rcode_counts"),
        "validators": {
            "by_severity": sc.get("by_severity"),
            "by_validator": sc.get("by_validator"),
            "ok": sc.get("ok"),
        },
        "pack": {
            "files": len(man.get("files", {})),
            "tokens": sum(i["tokens"] for i in man.get("files", {}).values()),
            "content_hash": man.get("content_hash"),
            "checked_by": man.get("checked_by"),
            "critic_vendor": man.get("critic_vendor"),
        },
        "tasks": {
            "count": len(tasks),
            "max_read_tokens": max(reads) if reads else 0,
            "budget": man.get("task_read_budget"),
            "over_budget": sum(
                1 for r in reads if man.get("task_read_budget") and r > man["task_read_budget"]
            ),
        },
        "events": len(events),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    r = report(a.run)
    text = json.dumps(r, indent=1, sort_keys=True)
    print(text)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / f"{r['run_id']}.report.json").write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
