"""The owner's spot-check (doc 14 section 4): 20 items per run, drawn by strata from a seed, judged one card at a time.

Sampling is deterministic: the same seed and the same committed runs always give the same cards. Per run, 20 items in four
exclusive strata, in this priority order: 8 high-impact items (fees, limits, rules, formats), 6 provider or integration
facts, 3 typed-data items (entity fields, screen sections, lifecycles), 3 ordinary items (comparable apps, journeys,
design conventions, trends...). A short stratum is topped up from the next ones and the card says so (``topped_up``).
Verdicts are saved after every answer, so a check can be resumed. The summary measures against the thresholds: supported
(ok plus not relevant) 95% or more, ok 90% or more, no invented detail on a high-impact item and fewer than 3 overall.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERDICTS = (
    "ok",
    "not_relevant",
    "claim_not_in_quote",
    "wrong_or_outdated",
    "wrong_place_or_company",
    "invented_detail",
    "skipped",
)
STRATA = ("high_impact", "service", "typed_data", "ordinary")
QUOTAS = {"high_impact": 8, "service": 6, "typed_data": 3, "ordinary": 3}
TYPED_KINDS = frozenset({"entity_pattern", "screen_pattern", "lifecycle"})
PASS = {"supported": 0.95, "ok": 0.90, "invented_total_below": 3}


def stratum_of(item: dict[str, Any]) -> str:
    if item.get("kind") == "service_option":
        return "service"
    if item.get("impact") == "high":
        return "high_impact"
    if item.get("kind") in TYPED_KINDS:
        return "typed_data"
    return "ordinary"


def _key(seed: int, run_id: str, item_id: str) -> str:
    return hashlib.sha256(f"{seed}:{run_id}:{item_id}".encode()).hexdigest()


def draw(
    items: list[dict[str, Any]], run_id: str, seed: int, per_run: int = 20
) -> list[dict[str, Any]]:
    """The cards of one run: strata quotas scaled to ``per_run``, shortfalls topped up in stratum order."""
    pool: dict[str, list[dict[str, Any]]] = {s: [] for s in STRATA}
    for it in items:
        if it.get("rejected"):
            continue
        pool[stratum_of(it)].append(it)
    for s in STRATA:
        pool[s].sort(key=lambda i: _key(seed, run_id, i["id"]))
    scale = per_run / 20
    want = {s: max(0, round(QUOTAS[s] * scale)) for s in STRATA}
    chosen: list[tuple[str, bool, dict[str, Any]]] = []
    taken: set[str] = set()
    for s in STRATA:
        for it in pool[s][: want[s]]:
            chosen.append((s, False, it))
            taken.add(it["id"])
    short = per_run - len(chosen)
    if short > 0:  # top up from the strata that still have items, in order
        for s in STRATA:
            for it in pool[s]:
                if short <= 0:
                    break
                if it["id"] not in taken:
                    chosen.append((s, True, it))
                    taken.add(it["id"])
                    short -= 1
    cards = []
    for s, topped, it in chosen:
        q = it.get("best_quote") or {}
        cards.append(
            {
                "key": f"{run_id}:{it['id']}",
                "run_id": run_id,
                "item_id": it["id"],
                "stratum": s,
                "topped_up": topped,
                "job": it["job"],
                "kind": it["kind"],
                "claim": it["claim"],
                "label": it["label"],
                "data": {
                    k: v for k, v in (it.get("data") or {}).items() if v not in (None, "", [], {})
                },
                "quote": q.get("text", ""),
                "source": {
                    "domain": q.get("domain"),
                    "url": q.get("url"),
                    "published": q.get("published"),
                },
                "evidence": it.get("evidence"),
                "impact": it.get("impact"),
            }
        )
    return cards


def summarize(check: dict[str, Any]) -> dict[str, Any]:
    cards = {c["key"]: c for c in check["cards"]}
    answered = {k: v for k, v in check["verdicts"].items() if k in cards and v != "skipped"}
    counts: dict[str, int] = dict.fromkeys(VERDICTS, 0)
    for v in check["verdicts"].values():
        counts[v] = counts.get(v, 0) + 1
    n = len(answered)
    ok = counts["ok"]
    supported = ok + counts["not_relevant"]
    invented = [k for k, v in answered.items() if v == "invented_detail"]
    inv_high = [k for k in invented if cards[k].get("impact") == "high"]
    supported_pct = round(supported / n, 4) if n else None
    ok_pct = round(ok / n, 4) if n else None
    ok_pass = supported_pct is not None and supported_pct >= PASS["supported"]
    strict_pass = ok_pct is not None and ok_pct >= PASS["ok"]
    inv_pass = not inv_high and len(invented) < PASS["invented_total_below"]
    bad_by: dict[str, int] = {}
    for k, v in answered.items():
        if v not in ("ok", "not_relevant"):
            c = cards[k]
            for label in (f"job:{c['job']}", f"kind:{c['kind']}"):
                bad_by[label] = bad_by.get(label, 0) + 1
    worst = sorted(bad_by.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "counts": counts,
        "answered": n,
        "total": len(cards),
        "supported_pct": supported_pct,
        "ok_pct": ok_pct,
        "invented_high_impact": len(inv_high),
        "invented_total": len(invented),
        "pass": {"supported": ok_pass, "ok": strict_pass, "no_invented": inv_pass},
        "passed": bool(n and ok_pass and strict_pass and inv_pass),
        "complete": n + counts["skipped"] >= len(cards),
        "worst": [{"where": k, "count": c} for k, c in worst],
        "thresholds": {
            "supported": PASS["supported"],
            "ok": PASS["ok"],
            "invented_total_below": PASS["invented_total_below"],
        },
    }


def export_markdown(check: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        f"# Spot-check {check['id']}",
        "",
        f"- Seed: {check['seed']}; runs: {', '.join(check['run_ids'])}",
        f"- Answered {summary['answered']} of {summary['total']}; supported {summary['supported_pct']}; ok {summary['ok_pct']}",
        f"- Invented detail: {summary['invented_total']} (high impact {summary['invented_high_impact']})",
        f"- Result: **{'PASSED' if summary['passed'] else 'NOT PASSED'}**",
        "",
        "| Verdict | Count |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in summary["counts"].items()]
    if summary["worst"]:
        lines += [
            "",
            "Where it went wrong: "
            + ", ".join(f"{w['where']} ({w['count']})" for w in summary["worst"]),
        ]
    lines += ["", "## Cards", ""]
    for c in check["cards"]:
        v = check["verdicts"].get(c["key"], "not answered")
        lines += [
            f"### {c['label']} ({c['kind']}, {c['stratum']}{', topped up' if c['topped_up'] else ''})",
            f"Verdict: **{v}**",
            "",
            c["claim"],
            "",
            f"> {c['quote']}",
            f"{c['source'].get('domain') or ''} {c['source'].get('url') or ''}",
            "",
        ]
    return "\n".join(lines)


@dataclass
class SpotCheckStore:
    """Spot-checks as one JSON file each under ``<data>/spot-checks/``; written atomically after every change."""

    root: Path

    def _path(self, check_id: str) -> Path:
        if not check_id.replace("_", "").isalnum():
            raise KeyError(check_id)
        return self.root / f"{check_id}.json"

    def save(self, check: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(check["id"])
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)

    def get(self, check_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(check_id).read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (OSError, ValueError, KeyError):
            return None

    def list(self) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.root.glob("*.json")) if self.root.is_dir() else []:
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = summarize(c)
            out.append(
                {
                    "id": c["id"],
                    "seed": c["seed"],
                    "run_ids": c["run_ids"],
                    "created_at": c["created_at"],
                    "answered": s["answered"],
                    "total": s["total"],
                    "passed": s["passed"],
                }
            )
        return sorted(out, key=lambda c: c["created_at"], reverse=True)
