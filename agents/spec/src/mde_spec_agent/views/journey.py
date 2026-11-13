"""One journey (``GET /api/runs/{id}/views/journey/{flow_id}``): stories, the emotion line, moments that matter, unhappy
paths, and the steps by their DECLARED entries and exits (never guessed)."""

from __future__ import annotations

import re
from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core.graph import Screen, SpecGraph

from ._data import words
from .names import name_of, plain

_ARROW = re.compile(r"\s*(?:->|→|─►|=>|—>)\s*")


def resolve_role(graph: SpecGraph, role: str | None) -> str | None:
    """A role id from an id or a name (case-insensitive); ``None`` when empty or unknown."""
    if not role:
        return None
    if role in graph.roles:
        return role
    low = role.strip().lower()
    for rid, r in sorted(graph.roles.items()):
        if r.name.lower() == low:
            return rid
    return None


def exits_of(graph: SpecGraph, s: Screen) -> list[dict[str, str]]:
    """Where a screen can go: its action effects first (labelled by the action), then plain links."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a in sorted(s.actions, key=lambda x: x.key):
        for st in a.effect:
            if st.verb == "go" and st.target and st.target.startswith("SCR-"):
                key = (humanize(a.key), st.target)
                if key not in seen:
                    seen.add(key)
                    out.append({"label": key[0], "to": st.target})
    linked = {x["to"] for x in out}
    for t in sorted(s.leads_to):
        if t.startswith("SCR-") and t not in linked:
            tt = graph.screens.get(t)
            out.append({"label": f"Go to {tt.name if tt else name_of(graph, t)}", "to": t})
    return out


def _split_unhappy(text: str, graph: SpecGraph) -> dict[str, str]:
    parts = _ARROW.split(plain(text, graph), maxsplit=1)
    return {"what": parts[0].strip(), "recovery": parts[1].strip() if len(parts) > 1 else ""}


def journey_view(
    graph: SpecGraph, flow_id: str, role: str | None = None, profile: str = "typical"
) -> dict[str, Any]:
    flow = graph.flows[flow_id]
    w = words()
    rid = resolve_role(graph, role) or (flow.role_ids[0] if flow.role_ids else None)
    role_name = graph.roles[rid].name if rid in graph.roles else ""
    ordered: list[str] = []
    for st in flow.stages:
        for sid in st.screen_ids:
            if sid in graph.screens and sid not in ordered:
                ordered.append(sid)
    mine = [
        sid
        for sid in ordered
        if not graph.screens[sid].role_ids or rid is None or rid in graph.screens[sid].role_ids
    ]
    chosen = mine or ordered
    in_journey = set(chosen)
    exits = {sid: exits_of(graph, graph.screens[sid]) for sid in chosen}
    steps: list[dict[str, Any]] = []
    goal_of: dict[str, str] = {}
    for st in flow.stages:
        for sid in st.screen_ids:
            goal_of.setdefault(sid, plain(st.goal, graph))
    for sid in chosen:
        s = graph.screens[sid]
        entries = [
            graph.screens[o].name
            for o in chosen
            if o != sid and any(x["to"] == sid for x in exits[o])
        ]
        steps.append(
            {
                "screen_id": sid,
                "title": s.name,
                "says": goal_of.get(sid) or plain(s.purpose, graph),
                "entries": entries,
                "exits": exits[sid],
            }
        )
    levels = w["emotion_levels"]
    emotion = []
    for stg in flow.stages:
        feel = w["emotion_words"].get(stg.emotion, stg.emotion)
        if stg.emotion_assumed:
            feel += " (the spec's reading)"
        emotion.append(
            {
                "label": f"{stg.n}. {plain(stg.goal, graph)}",
                "level": levels.get(stg.emotion, 3),
                "feeling": feel,
            }
        )
    by_n = {stg.n: stg for stg in flow.stages}
    moments = [
        f"Step {n}: {plain(by_n[n].goal, graph)}"
        for n in sorted(flow.moments_that_matter)
        if n in by_n
    ]
    ids_in = set(chosen) | in_journey
    stories = []
    for _, st in sorted(graph.stories.items()):
        if st.status != "in":
            continue
        if (rid is None or st.role_id == rid) and (set(st.screen_ids) & ids_in):
            stories.append(
                f"As {name_of(graph, st.role_id)}, I want {plain(st.want, graph)} so that {plain(st.so_that, graph)}."
            )
    if not stories and rid:
        for _, st in sorted(graph.stories.items()):
            if st.role_id == rid and st.status == "in" and len(stories) < 10:
                stories.append(
                    f"As {role_name}, I want {plain(st.want, graph)} so that {plain(st.so_that, graph)}."
                )
    return {
        "id": flow.id,
        "title": flow.name,
        "role": role_name,
        "roles": [name_of(graph, r) for r in flow.role_ids],
        "variant": flow.variant,
        "stories": stories,
        "emotion_line": emotion,
        "emotion_note": w["emotion_assumed_note"]
        if any(s.emotion_assumed for s in flow.stages)
        else "",
        "moments_that_matter": moments,
        "unhappy_paths": [_split_unhappy(u, graph) for u in flow.unhappy_paths],
        "steps": steps,
        "mock_profile": profile if profile in w["mock_profile_words"] else flow.mock_profile,
    }
