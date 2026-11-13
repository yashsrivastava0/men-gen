"""Plain-word building blocks shared by the G1.5 and G2 views (apps, roles, entities)."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.derive.text import humanize, plural
from mde_spec_core.graph import Entity, Role, SpecGraph

from ._data import words
from .names import join_words, name_of, plain
from .phases import module_title, release_ids, screen_title

_LEVEL = ("none", "view", "create", "edit", "delete", "manage")
_SURFACE = {"phone": "phone app", "tablet": "tablet app", "web": "web portal"}


def field_names(e: Entity, limit: int = 12) -> list[str]:
    out = [
        humanize(f.key).lower()
        for f in e.fields
        if f.type not in ("id", "secret") and f.key != "id"
    ]
    if len(out) > limit:
        extra = len(out) - limit
        out = [*out[:limit], f"and {extra} more"]
    return out


def entity_plain(graph: SpecGraph, e: Entity) -> str:
    """One entity in plain words: what is kept, the steps it goes through, who owns it."""
    parts = [f"The app keeps a record for each {e.name.lower()}"]
    fields = field_names(e)
    if fields:
        parts[0] += ": " + join_words(fields)
    parts[0] += "."
    if e.lifecycle.states:
        parts.append(
            "It moves through these steps: "
            + " then ".join(s.replace("_", " ") for s in e.lifecycle.states)
            + "."
        )
    if e.owner_module and graph.modules.get(e.owner_module):
        parts.append(f"It belongs to the {graph.modules[e.owner_module].name} part.")
    if e.later:
        parts.append("It is only needed by features that come in a later release.")
    return " ".join(parts)


def entities_plain(graph: SpecGraph) -> list[dict[str, Any]]:
    return [
        {"name": e.name, "plain": entity_plain(graph, e), "fields": field_names(e)}
        for _, e in sorted(graph.entities.items())
        if e.status == "in"
    ]


def role_access(graph: SpecGraph, role: Role) -> list[str]:
    w = words()["access_words"]
    out: list[str] = []
    apps = [graph.apps[a].name for a in role.app_ids if a in graph.apps]
    if apps:
        out.append("Works in " + join_words(apps))
    for _, e in sorted(graph.entities.items()):
        if e.status != "in" or e.later:
            continue
        best = "none"
        for row in e.access:
            if row.role_id == role.id:
                for a in row.actions:
                    if _LEVEL.index(a) > _LEVEL.index(best):
                        best = a
        if best != "none":
            out.append(f"Can {w[best]} {plural(e.name.lower())}")
    return out


def roles_plain(graph: SpecGraph, derived: Any = None) -> list[dict[str, Any]]:
    out = []
    for _, r in sorted(graph.roles.items()):
        if r.status != "in":
            continue
        home = graph.screens.get(r.home_screen) if r.home_screen else None
        out.append(
            {
                "name": r.name,
                "goal": plain(r.goal, graph),
                "access": role_access(graph, r),
                "home": home.name if home else "",
            }
        )
    return out


def app_map(graph: SpecGraph, derived: Any = None) -> list[dict[str, Any]]:
    release, _ = release_ids(graph, derived)
    rel = set(release)
    out = []
    for aid, a in sorted(graph.apps.items()):
        mods = [graph.modules[m].name for m in a.module_ids if m in graph.modules]
        out.append(
            {
                "name": a.name,
                "kind": _SURFACE.get(a.surface, a.surface),
                "screens": sum(
                    1 for sid, s in graph.screens.items() if s.app_id == aid and sid in rel
                ),
                "modules": mods,
                "roles": [graph.roles[r].name for r in a.role_ids if r in graph.roles],
            }
        )
    return out


def inventory_rows(graph: SpecGraph, derived: Any) -> list[dict[str, str]]:
    ph = words()["phases"]
    release, deferred = release_ids(graph, derived)
    rel = set(release)
    inv = getattr(derived, "inventory", None)
    ids = [p.id for p in inv.screens] if inv is not None else sorted(rel | set(deferred))
    return [
        {
            "id": sid,
            "title": screen_title(graph, derived, sid),
            "module": module_title(graph, derived, sid),
            "phase": ph["one"]["name"] if sid in rel else ph["two"]["name"],
        }
        for sid in sorted(ids)
    ]


def node_label(graph: SpecGraph, node_id: str) -> str:
    return name_of(graph, node_id)
