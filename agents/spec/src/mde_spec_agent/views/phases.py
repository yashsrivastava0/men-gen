"""The release split in plain words: Phase 1 and Phase 2, with what was pulled in and why (shared by preview and G1.5)."""

from __future__ import annotations

from typing import Any

from mde_spec_core.graph import SpecGraph

from ._data import words
from .names import join_words, name_of, plain


def release_ids(graph: SpecGraph, derived: Any) -> tuple[list[str], dict[str, str]]:
    """``(release screen ids, deferred screen id -> reason)``; from the plan when there is one, else from the graph."""
    plan = getattr(derived, "plan", None)
    if plan is not None:
        return sorted(plan.release_screens), dict(plan.deferred_screens)
    ids = sorted(sid for sid, s in graph.screens.items() if s.status == "in")
    later = {
        sid: (s.later_reason or "first_release")
        for sid, s in graph.screens.items()
        if s.status != "in"
    }
    return ids, later


def screen_title(graph: SpecGraph, derived: Any, sid: str) -> str:
    s = graph.screens.get(sid)
    if s is not None and s.name:
        return s.name
    inv = getattr(derived, "inventory", None)
    p = inv.proposal(sid) if inv is not None else None
    return p.name if p is not None else name_of(graph, sid)


def module_title(graph: SpecGraph, derived: Any, sid: str) -> str:
    s = graph.screens.get(sid)
    mid = s.module_id if s is not None else None
    if mid is None:
        inv = getattr(derived, "inventory", None)
        p = inv.proposal(sid) if inv is not None else None
        mid = p.module if p is not None else None
    if mid is None:
        return ""
    m = graph.modules.get(mid)
    if m is not None:
        return m.name
    inv = getattr(derived, "inventory", None)
    if inv is not None:
        for dm in inv.derived_modules:
            if dm[0] == mid:
                return dm[1]
        if inv.system_module and inv.system_module[0] == mid:
            return inv.system_module[1]
    return name_of(graph, mid)


def phase_split(graph: SpecGraph, derived: Any, cap: int) -> list[dict[str, Any]]:
    """Phase 1 and Phase 2 as ``{name, count, items, why, pulled_in}`` (the shape the G1.5 page reads)."""
    w = words()
    ph = w["phases"]
    release, deferred = release_ids(graph, derived)
    plan = getattr(derived, "plan", None)
    pulled: list[str] = []
    if plan is not None:
        for p in plan.pulled_in:
            why = w["pull_in_rules"].get(p.rule, "the first release needs it")
            pulled.append(f"{screen_title(graph, derived, p.screen_id)}: {why}")
    one_why = ph["why_one"]
    if pulled:
        one_why += f" {len(pulled)} screen{'s were' if len(pulled) != 1 else ' was'} pulled in (listed below)."
    by_reason: dict[str, int] = {}
    for r in deferred.values():
        by_reason[r] = by_reason.get(r, 0) + 1
    parts = []
    for reason in ("user", "capacity", "first_release"):
        n = by_reason.get(reason)
        if n:
            text = w["later_reasons"][reason].format(cap=cap)
            parts.append(f"{n} because {text}")
    for reason, n in sorted(by_reason.items()):
        if reason not in ("user", "capacity", "first_release"):
            parts.append(f"{n} because {w['later_reasons']['default']}")
    two_why = (
        f"{ph['why_two_prefix']}: " + join_words(parts) + "."
        if parts
        else "Nothing waits for a later release."
    )
    return [
        {
            "name": ph["one"]["name"],
            "count": len(release),
            "items": [screen_title(graph, derived, s) for s in release],
            "why": one_why,
            "pulled_in": pulled,
        },
        {
            "name": ph["two"]["name"],
            "count": len(deferred),
            "items": [screen_title(graph, derived, s) for s in sorted(deferred)],
            "why": two_why,
            "pulled_in": [],
        },
    ]


def explain(text: str, graph: SpecGraph) -> str:
    return plain(text, graph)
