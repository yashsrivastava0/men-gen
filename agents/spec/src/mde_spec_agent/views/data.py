"""Data and contract (``GET /api/runs/{id}/views/data``): entities and lifecycles, operations, fixtures, mock behaviours and
integrations with their interrupted-flow and pending-timeout rules, all in plain words."""

from __future__ import annotations

import random
from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core.graph import Entity, SpecGraph
from mde_spec_core.mock import build_behaviours

from .common import entity_plain
from .names import join_words, name_of, plain
from .screen import _fields_text, mock_profiles

_METHOD_WORDS = {
    "GET": "reads",
    "POST": "creates or runs",
    "PUT": "replaces",
    "PATCH": "changes",
    "DELETE": "removes",
}


def _entity(graph: SpecGraph, e: Entity) -> dict[str, Any]:
    trans = [
        f"{t.from_state.replace('_', ' ')} to {t.to_state.replace('_', ' ')}"
        + (f" (by {join_words([name_of(graph, r) for r in t.roles])})" if t.roles else "")
        for t in e.lifecycle.transitions
    ]
    return {
        "name": e.name,
        "plain": entity_plain(graph, e),
        "lifecycle": [s.replace("_", " ") for s in e.lifecycle.states],
        "transitions": trans,
        "fields": [
            f"{humanize(f.key).lower()} ({f.type}{', required' if f.required else ''})"
            for f in e.fields
            if f.type != "id" and f.key != "id"
        ],
        "later": bool(e.later),
    }


def _operations(graph: SpecGraph) -> list[dict[str, Any]]:
    out = []
    for _, op in sorted(graph.operations.items()):
        ent = graph.entities.get(op.entity or "")
        out.append(
            {
                "name": op.name,
                "does": f"{_METHOD_WORDS.get(op.method, op.method.lower())} {ent.name.lower() if ent else 'data'}",
                "method": op.method,
                "path": op.path,
                "who": ", ".join(name_of(graph, r) for r in op.roles) or "everyone allowed",
                "can_fail_with": ", ".join(sorted(e.code.replace("_", " ") for e in op.errors)),
                "safe_to_repeat": bool(op.idempotent),
            }
        )
    return out


def _fixtures(
    graph: SpecGraph, mocks: dict[str, dict[str, Any]], per_entity: int = 3, cap: int = 150
) -> list[dict[str, Any]]:
    typical = mocks.get("typical", {}).get("entities", {})
    out: list[dict[str, Any]] = []
    for eid in sorted(typical):
        e = graph.entities.get(eid)
        for rec in typical[eid].get("rows", [])[:per_entity]:
            out.append(
                {
                    "record": e.name if e else name_of(graph, eid),
                    "state": str(rec.get("state") or "").replace("_", " "),
                    "shows": _fields_text(rec, None),
                    "example_id": rec.get("id", ""),
                }
            )
            if len(out) >= cap:
                return out
    return out


def _mock_behaviours(graph: SpecGraph) -> list[dict[str, str]]:
    b = build_behaviours(graph, random.Random(0))  # noqa: S311 - the table is data; the generator does not draw
    rows: list[dict[str, str]] = []
    classes: dict[str, int] = {}
    for v in b["latency"].values():
        classes[v["class"]] = classes.get(v["class"], 0) + 1
    if classes:
        rows.append(
            {
                "behaviour": "How fast the mock server answers",
                "detail": "; ".join(
                    f"{n} request(s) answer {c}" for c, n in sorted(classes.items())
                ),
            }
        )
    if b.get("paging"):
        rows.append(
            {
                "behaviour": "Lists arrive in pages",
                "detail": ", ".join(
                    f"{humanize(k).lower()} {v}" for k, v in sorted(b["paging"].items())
                ),
            }
        )
    if b.get("auth_expiry"):
        rows.append(
            {
                "behaviour": "The sign-in expires",
                "detail": ", ".join(
                    f"{humanize(k).lower()} {v}" for k, v in sorted(b["auth_expiry"].items())
                ),
            }
        )
    for r in b.get("realtime_scripts", []):
        steps = " then ".join(s["to_state"].replace("_", " ") for s in r["script"])
        rows.append(
            {
                "behaviour": f"Live updates of {name_of(graph, r['entity'])}",
                "detail": steps or "no steps",
            }
        )
    for c in b.get("provider_callbacks", []):
        rows.append(
            {
                "behaviour": f"{name_of(graph, c['integration'])} answers later",
                "detail": "outcomes: "
                + ", ".join(o["event"].replace("_", " ") for o in c["outcomes"]),
            }
        )
    return rows


def _integrations(graph: SpecGraph) -> list[dict[str, Any]]:
    out = []
    for _, i in sorted(graph.integrations.items()):
        out.append(
            {
                "name": i.name,
                "what": plain(i.flow, graph),
                "if_interrupted": plain(i.interruption, graph),
                "pending_timeout": plain(i.pending_timeout, graph),
                "events": ", ".join(i.events),
                "mode": {
                    "mock": "pretend (mock)",
                    "adapter": "through an adapter",
                    "real": "real",
                }.get(i.mode, i.mode),
                "leaves_the_app": bool(i.provider_side_flow),
            }
        )
    return out


def data_view(graph: SpecGraph, *, mock: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    mocks = mock if mock is not None else mock_profiles(graph)
    return {
        "entities": [
            _entity(graph, e) for _, e in sorted(graph.entities.items()) if e.status == "in"
        ],
        "operations": _operations(graph),
        "fixtures": _fixtures(graph, mocks),
        "mock_behaviours": _mock_behaviours(graph),
        "integrations": _integrations(graph),
        "behaviours": [
            {"category": humanize(b.category), "text": plain(b.text, graph)}
            for _, b in sorted(graph.behaviors.items())
        ],
    }
