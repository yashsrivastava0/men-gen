"""The navigation graph (``GET /api/runs/{id}/views/graph``): screens, routes, in or later, unreachable screens and dead
ends, by the SAME reachability as validator V-04 (entry points: role homes, auth and onboarding screens, the first screen
of each app; a screen is reached by a link or by the 'go' effect of an action)."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core.graph import Screen, SpecGraph
from mde_spec_core.validators.structure import entry_screens

from .journey import resolve_role
from .names import name_of

TERMINAL_TYPES = {
    "confirmation",
    "legal",
    "status",
    "permission",
}  # the same set V-04 exempts from the dead-end rule


def targets(s: Screen) -> set[str]:
    out = set(s.leads_to)
    for a in s.actions:
        out |= {st.target for st in a.effect if st.verb == "go" and st.target}
    return {t for t in out if t.startswith("SCR-")}


def reachability(graph: SpecGraph) -> tuple[set[str], list[str], list[str]]:
    """``(entries, unreachable ids, dead-end ids)`` over the screens that are `in`, exactly as V-04 computes them."""
    live = {sid: s for sid, s in graph.screens.items() if s.status == "in"}
    seen: set[str] = set()
    entries = {e for e in entry_screens(graph) if e in live}
    stack = list(entries)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(t for t in targets(live[cur]) if t in live)
    unreachable = sorted(set(live) - seen)
    dead = sorted(
        sid for sid, s in live.items() if not targets(s) and s.screen_type not in TERMINAL_TYPES
    )
    return entries, unreachable, dead


def nav_graph(graph: SpecGraph, role: str | None = None, derived: Any = None) -> dict[str, Any]:
    rid = resolve_role(graph, role)
    entries, unreachable, dead = reachability(graph)
    nodes: dict[str, dict[str, Any]] = {}
    for sid, s in sorted(graph.screens.items()):
        if rid is not None and s.role_ids and rid not in s.role_ids:
            continue
        nodes[sid] = {
            "id": sid,
            "title": s.name,
            "app": name_of(graph, s.app_id),
            "in": True if s.status == "in" else "later",
        }
    plan = getattr(derived, "plan", None)
    if (
        plan is not None and rid is None
    ):  # screens that wait for a later release, drawn dashed and unlinked
        inv = derived.inventory
        for sid in sorted(plan.deferred_screens):
            p = inv.proposal(sid)
            if p is not None and sid not in nodes:
                nodes[sid] = {
                    "id": sid,
                    "title": p.name,
                    "app": name_of(graph, p.app),
                    "in": "later",
                }
    edges = []
    for sid in sorted(nodes):
        s = graph.screens.get(sid)
        if s is None:
            continue
        labelled: dict[str, str] = {}
        for a in sorted(s.actions, key=lambda x: x.key):
            for st in a.effect:
                if st.verb == "go" and st.target:
                    labelled.setdefault(st.target, humanize(a.key))
        for t in sorted(targets(s)):
            if t in nodes:
                edges.append({"from": sid, "to": t, "label": labelled.get(t, "")})
    return {
        "roles": [r.name for _, r in sorted(graph.roles.items()) if r.status == "in"],
        "role": graph.roles[rid].name if rid else "",
        "entries": sorted(e for e in entries if e in nodes),
        "nodes": list(nodes.values()),
        "edges": edges,
        "unreachable": [x for x in unreachable if x in nodes],
        "dead_ends": [x for x in dead if x in nodes],
    }
