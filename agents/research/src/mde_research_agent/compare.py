"""The quality gate for a discovery change: compare a new run with an old one on the SAME Brief.

``uv run python -m mde_research_agent.compare <old run folder> <new run folder> [--json]``

A change to how the agent searches or reads may lower the cost only if the result that goes to the Spec stays equal or gets
better. The gate (owner, 2026-11-11) is five measures plus a floor test, all read from ``research.json`` and the evidence
file of a run:

1. required slots filled: not lower;
2. verified items per required slot: not lower;
3. official share of the items: not lower;
4. number-rejection rate (``number_not_in_quote`` over everything extracted): not higher;
5. the floor test: every (slot, entity) the old run verified with official or 2+ independent source evidence reappears in
   the new run, at least 90% of them;
6. reported, not gated: the checker's ``not_supported`` rate (0 over many verdicts means the checker is unproven).

Cost is shown beside quality (cost per required slot filled), never instead of it. Pure functions over loaded JSON; the
folder reader is the only I/O. One run per side is one sample: the web and the model move between runs, so a small
difference is noise and the report says how many samples it rests on.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

from mde_research_agent.core.text import stem, tokens

FLOOR_SHARE = 0.90
STRONG_EVIDENCE = ("official", "corroborated")


def load_run(folder: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(research, evidence)`` of one run folder (``research.json`` and ``evidence.json.gz``)."""
    research = json.loads((folder / "research.json").read_text(encoding="utf-8"))
    with gzip.open(folder / "evidence.json.gz", "rt", encoding="utf-8") as fh:
        evidence = json.load(fh)
    return research, evidence


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _stems(text: str) -> frozenset[str]:
    return frozenset(stem(t) for t in tokens(text))


def _same_entity(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two entity keys are the same when one holds the other or they share at least half of their words."""
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    return len(a & b) / len(a | b) >= 0.5


def strong_entities(
    research: dict[str, Any], evidence: dict[str, Any]
) -> list[tuple[str, str, frozenset[str]]]:
    """``(slot or kind, item id, entity words)`` of every item with official or 2+ independent source evidence."""
    keys = {
        a["item_id"]: a.get("norm_key") or "" for a in evidence.get("atoms", []) if a.get("item_id")
    }
    out = []
    for it in research.get("items", []):
        if it.get("evidence") in STRONG_EVIDENCE or it.get("independent_domains", 0) >= 2:
            out.append(
                (
                    str(it.get("slot") or it.get("kind")),
                    str(it["id"]),
                    _stems(keys.get(it["id"]) or it.get("label", "")),
                )
            )
    return out


def floor_test(
    old: tuple[dict[str, Any], dict[str, Any]], new: tuple[dict[str, Any], dict[str, Any]]
) -> dict[str, Any]:
    """The share of the old run's strong (slot, entity) pairs that the new run has again."""
    before = strong_entities(*old)
    after = strong_entities(*new)
    missing = [
        (slot, item_id)
        for slot, item_id, words in before
        if not any(s == slot and _same_entity(words, w) for s, _, w in after)
    ]
    return {
        "old_strong": len(before),
        "reappear": len(before) - len(missing),
        "share": _ratio(len(before) - len(missing), len(before)),
        "missing": [{"slot": s, "old_item": i} for s, i in missing],
    }


def run_numbers(research: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    items = research.get("items", [])
    cov = research.get("coverage", {})
    extracted = evidence.get("extraction", {}).get("extracted", 0)
    dropped = research.get("dropped", {})
    atoms = [a for a in evidence.get("atoms", []) if a.get("verdict")]
    verdicts = [a["verdict"] for a in atoms]
    budget = research.get("budget", {})
    filled = cov.get("slots_filled", 0)
    required = cov.get("slots_required", 0)
    official = sum(1 for i in items if i.get("evidence") == "official")
    return {
        "status": research.get("status"),
        "slots_filled": filled,
        "slots_required": required,
        "items": len(items),
        "items_per_required_slot": _ratio(len(items), required),
        "official": official,
        "official_share": _ratio(official, len(items)),
        "extracted": extracted,
        "number_rejections": dropped.get("number_not_in_quote", 0),
        "number_rejection_rate": _ratio(dropped.get("number_not_in_quote", 0), extracted),
        "figures_dropped": dropped.get("figures_dropped", 0),
        "checker_verdicts": len(verdicts),
        "checker_not_supported": _ratio(verdicts.count("not_supported"), len(verdicts)),
        "searches": budget.get("searches_used"),
        "pages_read": len(evidence.get("pages", [])),
        "usd_micros": budget.get("usd_micros"),
        "micros_per_slot_filled": _ratio(budget.get("usd_micros") or 0, filled),
        "micros_per_item": _ratio(budget.get("usd_micros") or 0, len(items)),
        "validation_errors": (research.get("validation") or {}).get("errors", 0),
    }


def compare(
    old: tuple[dict[str, Any], dict[str, Any]], new: tuple[dict[str, Any], dict[str, Any]]
) -> dict[str, Any]:
    """Both runs' numbers, the floor test and the verdict of the gate (``passed`` and the reasons it failed)."""
    a, b = run_numbers(*old), run_numbers(*new)
    floor = floor_test(old, new)
    failed: list[str] = []

    def lower(key: str) -> bool:
        return (b[key] or 0) < (a[key] or 0)

    if lower("slots_filled"):
        failed.append(f"required slots filled fell: {a['slots_filled']} -> {b['slots_filled']}")
    if lower("items_per_required_slot"):
        failed.append("verified items per required slot fell")
    if lower("official_share"):
        failed.append(f"official share fell: {a['official_share']} -> {b['official_share']}")
    if (b["number_rejection_rate"] or 0) > (a["number_rejection_rate"] or 0):
        failed.append(
            f"number-rejection rate rose: {a['number_rejection_rate']} -> {b['number_rejection_rate']}"
        )
    if floor["share"] is not None and floor["share"] < FLOOR_SHARE:
        failed.append(f"floor test {floor['share']} is below {FLOOR_SHARE}")
    return {
        "old": a,
        "new": b,
        "floor": floor,
        "passed": not failed,
        "failed": failed,
        "samples": "1 vs 1",
    }


def render(result: dict[str, Any]) -> str:
    a, b = result["old"], result["new"]
    rows = [
        ("status", "status"),
        ("required slots filled", "slots_filled"),
        ("required slots", "slots_required"),
        ("items (store + appendix)", "items"),
        ("items per required slot", "items_per_required_slot"),
        ("official share", "official_share"),
        ("number rejections", "number_rejections"),
        ("number-rejection rate", "number_rejection_rate"),
        ("figures dropped, item kept", "figures_dropped"),
        ("checker not_supported rate", "checker_not_supported"),
        ("checker verdicts", "checker_verdicts"),
        ("validation errors", "validation_errors"),
        ("searches", "searches"),
        ("pages read", "pages_read"),
        ("cost (micro-USD)", "usd_micros"),
        ("micro-USD per slot filled", "micros_per_slot_filled"),
        ("micro-USD per item", "micros_per_item"),
    ]
    lines = [f"{'measure':<30}{'old':>14}{'new':>14}"]
    lines += [f"{label:<30}{a[k]!s:>14}{b[k]!s:>14}" for label, k in rows]
    f = result["floor"]
    lines.append(
        f"{'floor test (strong pairs back)':<30}{f['old_strong']!s:>14}{(str(f['reappear']) + ' (' + str(f['share']) + ')'):>14}"
    )
    lines.append(
        "GATE: " + ("PASSED" if result["passed"] else "FAILED: " + "; ".join(result["failed"]))
    )
    lines.append(f"samples: {result['samples']} (one run per side: a small difference is noise)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Compare two Research runs on the same Brief (the quality gate)."
    )
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    args = p.parse_args(argv)
    result = compare(load_run(args.old), load_run(args.new))
    sys.stdout.write((json.dumps(result, indent=2) if args.json else render(result)) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
