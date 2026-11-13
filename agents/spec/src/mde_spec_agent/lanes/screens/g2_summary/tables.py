"""The G2 tables, assembled by CODE from the graph and the check report (doc 18 1.1: tables from code, one paragraph from
the model): inventory, phases, the confirm list (top 20, money, legal, identity first), what differs from the Brief
(merged, split, added, phased), assumptions, degraded nodes and code fixes. Deterministic; no model."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.lanes.screens.common import lane_rules
from mde_spec_core.check import CONFIRM_TOP, confirm_list_ranked
from mde_spec_core.graph import SpecGraph

MAX_ROWS = 30


def differs(graph: SpecGraph, spec_input: Any, plan: dict[str, Any]) -> dict[str, list[str]]:
    merged = [
        f"{m.kind} {m.from_id} was folded into {m.into_id} ({m.reason})"
        for m in (getattr(spec_input, "merges", ()) or ())
    ]
    merged += [str(x) for x in plan.get("merged", [])]
    split = [str(x) for x in plan.get("split", [])]
    added = [
        f"{s.id} {s.name}: not asked for in the Brief, added so the app is complete"
        for _, s in sorted(graph.screens.items())
        if s.assumed and not s.system and s.status == "in"
    ]
    added += [str(x) for x in plan.get("added", [])]
    phased = [
        f"{f.id} {f.name}: {'later' if f.status == 'later' else 'out of scope'}"
        + (f" ({f.later_reason})" if f.later_reason else "")
        for _, f in sorted(graph.features.items())
        if f.status in ("later", "out")
    ]
    phased += [str(x) for x in plan.get("phased", [])]
    return {"merged": merged, "split": split, "added": added, "phased": phased}


def build_tables(
    graph: SpecGraph,
    *,
    research: Any,
    spec_input: Any,
    plan: dict[str, Any],
    flags: list[dict[str, Any]],
    rcode: Any,
) -> dict[str, Any]:
    live = [s for s in graph.screens.values() if s.status == "in"]
    apps = [
        {
            "id": a.id,
            "name": a.name,
            "screens": sum(1 for s in live if s.app_id == a.id),
            "system_screens": sum(1 for s in live if s.app_id == a.id and s.system),
        }
        for _, a in sorted(graph.apps.items())
    ]
    assumed = sorted(n.id for n in graph.all_nodes() if n.assumed)
    degraded = sorted(n.id for n in graph.all_nodes() if n.degraded)
    return {
        "inventory": {
            "apps": apps,
            "screens": len(live),
            "entities": sum(1 for e in graph.entities.values() if not e.later),
            "operations": len(graph.operations),
            "flows": len(graph.flows),
            "languages": list(graph.meta.languages),
        },
        "phases": {
            "in": sorted(f.id for f in graph.features.values() if f.status == "in"),
            "later": [
                {"id": f.id, "name": f.name, "reason": f.later_reason}
                for _, f in sorted(graph.features.items())
                if f.status == "later"
            ],
            "out": sorted(f.id for f in graph.features.values() if f.status == "out"),
        },
        "confirm": confirm_list_ranked(graph, research, CONFIRM_TOP),
        "differs": differs(graph, spec_input, plan),
        "assumptions": {"count": len(assumed), "nodes": assumed[:MAX_ROWS]},
        "degraded": {
            "count": len(degraded),
            "nodes": degraded[:MAX_ROWS],
            "flags": [f for f in flags if f.get("kind") == "degraded"][:MAX_ROWS],
        },
        "needs_decision": [f for f in flags if f.get("kind") == "needs_decision"][:MAX_ROWS],
        "suggestions": [f for f in flags if f.get("kind") == "suggestion"][:MAX_ROWS],
        "code_fixes": rcode.counts() if rcode is not None else {},
    }


def template_summary(graph: SpecGraph, tables: dict[str, Any]) -> dict[str, Any]:
    """The summary from code alone (FN-29): used whenever the paragraph call fails, or beside the paragraph."""
    t = lane_rules().g2_template
    inv = tables["inventory"]
    lines: list[str] = []
    d = tables["differs"]
    items = [f"{len(v)} {k}" for k, v in d.items() if v]
    lines.append(
        t["lines"]["differs"].format(items=", ".join(items))
        if items
        else t["lines"]["nothing_differs"]
    )
    if tables["assumptions"]["count"]:
        lines.append(t["lines"]["assumptions"].format(n=tables["assumptions"]["count"]))
    if tables["degraded"]["count"]:
        lines.append(t["lines"]["degraded"].format(n=tables["degraded"]["count"]))
    fixes = sum(tables["code_fixes"].values()) if tables["code_fixes"] else 0
    if fixes:
        lines.append(t["lines"]["fixes"].format(n=fixes))
    if tables["confirm"]:
        lines.append(
            t["lines"]["confirm"].format(
                n=len(tables["confirm"]), top=min(5, len(tables["confirm"]))
            )
        )
    return {
        "headline": t["headline"].format(
            screens=inv["screens"],
            apps=len(inv["apps"]),
            entities=inv["entities"],
            operations=inv["operations"],
            languages=len(inv["languages"]),
        ),
        "lines": lines,
    }
