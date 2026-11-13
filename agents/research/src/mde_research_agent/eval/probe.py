"""The consumer probe (doc 14 section 6): does Spec do better WITH the research than without?

A stand-in model does five Spec-like tasks twice: from the Brief alone (arm A) and from the Brief plus the research (arm B,
the compact main store). Every statement must cite finding ids or say ASSUMED. A judge call scores each statement:
supported, assumed or invented. Pass: arm B has more supported statements, fewer assumed ones, and no more invented ones
than arm A. A human should read ten "invented" verdicts. Tasks are data (``data/eval/tasks.yaml``).
"""

from __future__ import annotations

from typing import Any

import yaml

from mde_research_agent.assemble.store import compact_view
from mde_research_agent.calls import assemble as asm_call
from mde_research_agent.calls import eval_probe as probe_calls
from mde_research_agent.catalog.loader import DATA_DIR

MAX_FINDING_CHARS = 160_000  # about 40k tokens: the whole main store when it fits


def tasks() -> list[dict[str, str]]:
    data = yaml.safe_load((DATA_DIR / "eval" / "tasks.yaml").read_text(encoding="utf-8")) or {}
    return list(data.get("tasks", []))


def findings_text(
    research: dict[str, Any], need: str | None = None, lanes: dict[str, Any] | None = None
) -> str:
    """The main store as plain lines (``RI-001 [kind] label: claim``), for the research arm."""
    view = compact_view(research, asm_call.default_notes())
    lines = []
    for it in view["items"]:
        data = it.get("data") or {}
        extra = "; ".join(f"{k}={v}" for k, v in list(data.items())[:6])
        lines.append(
            f"{it['id']} [{it['kind']}] {it['label']}: {it['claim']} ({it.get('evidence')}) {extra}"[
                :600
            ]
        )
    return "\n".join(lines)[:MAX_FINDING_CHARS]


MAX_BRIEF_CHARS = 12_000


def _names(rows: Any, *keys: str) -> list[str]:
    out: list[str] = []
    for r in rows or []:
        if isinstance(r, str):
            out.append(r)
        elif isinstance(r, dict):
            text = next((str(r[k]) for k in keys if r.get(k)), "")
            if text:
                out.append(text)
    return out


def brief_from_input(inp: dict[str, Any]) -> str:
    """The Brief as plain lines, from a run's stored research input (what a Spec stand-in would be given)."""
    idea = inp.get("idea") or {}
    geo = inp.get("geography") or {}
    lines = [f"Idea: {idea.get('summary') or ''} ({idea.get('category') or 'no category'})"]
    where = ", ".join(
        str(x) for x in (*(geo.get("cities") or []), geo.get("region"), geo.get("country")) if x
    )
    if where:
        lines.append(f"Place: {where}")
    locales = (inp.get("locales") or {}).get("codes") or []
    if locales:
        lines.append("Languages: " + ", ".join(locales))
    for f in inp.get("features") or []:
        if isinstance(f, dict) and f.get("name"):
            lines.append(
                f"Feature {f.get('id', '')}: {f['name']} ({f.get('priority', 'nice')}) {f.get('intent', '')}"
            )
    for label, key in (
        ("Role", "roles"),
        ("App", "apps"),
        ("Module", "modules"),
        ("Entity", "entities"),
    ):
        for n in _names(inp.get(key), "name", "label", "title"):
            lines.append(f"{label}: {n}")
    for n in _names(inp.get("integrations"), "name", "label", "need"):
        lines.append(f"Integration: {n}")
    for fact in inp.get("facts") or []:
        if isinstance(fact, dict) and fact.get("statement"):
            lines.append(f"Fact: {str(fact['statement'])[:200]}")
    return "\n".join(lines)[:MAX_BRIEF_CHARS]


def brief_text(research: dict[str, Any], summary: str = "") -> str:
    """The Brief the stand-in sees: the real one when given, else a stub from the file's profile (said so, never silent)."""
    prof = research.get("profile", {})
    return (
        summary
        or "NO BRIEF WAS GIVEN (stub): "
        f"an app (size {prof.get('load_class')}, signals: {', '.join(prof.get('signals', [])) or 'none'})."
    )[:MAX_BRIEF_CHARS]


async def run_probe(runner: Any, research: dict[str, Any], *, brief: str = "") -> dict[str, Any]:
    findings = findings_text(research)
    ids = set(probe_calls.ID_RX.findall(findings))
    out: dict[str, Any] = {"tasks": [], "cost_micros": 0, "brief_given": bool(brief)}
    totals = {
        "A": {"supported": 0, "assumed": 0, "invented": 0},
        "B": {"supported": 0, "assumed": 0, "invented": 0},
    }
    for t in tasks():
        row: dict[str, Any] = {"task": t["id"], "need": t.get("need")}
        for arm, fx in (("A", ""), ("B", findings)):
            ans = await runner.run(
                probe_calls.PROBE_SPEC,
                probe_calls.ProbeIn(
                    task=t["instruction"], brief=brief_text(research, brief), findings=fx
                ),
            )
            out["cost_micros"] += ans.cost_micros
            statements = ans.output.statements
            if not statements:
                row[arm] = {"supported": 0, "assumed": 0, "invented": 0, "statements": 0}
                continue
            judged = await runner.run(
                probe_calls.JUDGE_SPEC,
                probe_calls.JudgeEvalIn(statements=statements, findings=findings),
            )
            out["cost_micros"] += judged.cost_micros
            counts = {"supported": 0, "assumed": 0, "invented": 0}
            for v in judged.output.verdicts:
                counts[v.verdict if v.verdict in counts else "invented"] += 1
            cited = {c for s in statements for c in s.cites if c in ids}
            row[arm] = {**counts, "statements": len(statements), "findings_cited": len(cited)}
            for k in counts:
                totals[arm][k] += counts[k]
        out["tasks"].append(row)
    out["totals"] = totals
    a, b = totals["A"], totals["B"]
    out["passed"] = bool(
        b["supported"] > a["supported"]
        and b["assumed"] <= a["assumed"]
        and b["invented"] <= a["invented"]
    )
    return out
