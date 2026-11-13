"""One screen as a CONTENT MAP (``GET /api/runs/{id}/views/screen/{sid}``): what it must communicate and do, never where.

Regions are grouped by importance and carry NO position and NO order (they are listed alphabetically, the way the pack
lists them); MUST, USUALLY and COULD are kept apart as three voices; actions say their intent and effect in words; states
carry their messages; the words are the source-language copy; example records come from the chosen mock profile. The label
'illustrative: the final layout is the front end's' belongs to the front end.
"""

from __future__ import annotations

import random
import re
from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core import vocab
from mde_spec_core.graph import Action, Region, Screen, SpecGraph
from mde_spec_core.mock import build_mock_profiles, profile_names
from mde_spec_core.render import bound_fields

from ._data import words
from .names import join_words, name_of, plain

MOCK_NAMES = ("empty", "typical", "busy", "extreme", "broken", "offline")
_TIME = re.compile(r"^now([+-])(\d+)([dhm])$")
_UNIT = {"d": "day", "h": "hour", "m": "minute"}


def mock_profiles(graph: SpecGraph) -> dict[str, dict[str, Any]]:
    """All six mock profiles of the program, seeded by the program (same graph, same records). Build once, reuse."""
    rnd = random.Random(f"{graph.meta.program_id}:{graph.meta.inputs_hash}")  # noqa: S311 - mock data, not security
    base = profile_names(graph.meta.profile)
    extra = [n for n in MOCK_NAMES if n not in base]
    return build_mock_profiles(graph, graph.meta.profile, rnd, extra=extra)


def _when(value: str) -> str:
    m = _TIME.match(value)
    if not m:
        return value
    sign, n, unit = m.groups()
    label = _UNIT[unit] + ("" if n == "1" else "s")
    return f"{n} {label} ago" if sign == "-" else f"in {n} {label}"


def _bind_text(graph: SpecGraph, b: str) -> str:
    if b.startswith("OP-"):
        op = graph.operations.get(b)
        return f"asks the server to {op.name.lower()}" if op else "asks the server"
    ent, _, fld = b.partition(".")
    e = graph.entities.get(ent)
    en = e.name if e else name_of(graph, ent)
    return f"{en}: {humanize(fld).lower()}" if fld else en


def _region(graph: SpecGraph, r: Region) -> dict[str, Any]:
    voice = words()["region_importance_voice"].get(r.importance, "usually")
    items = [_bind_text(graph, b) for b in sorted(r.bind)]
    return {
        "name": humanize(r.key),
        "importance": voice,
        "level": r.importance,
        "text": plain(r.what, graph),
        "items": items,
        "shape": r.shape,
    }


def _extra_regions(graph: SpecGraph, s: Screen) -> list[dict[str, Any]]:
    w = words()
    out: list[dict[str, Any]] = []
    for u in s.usually:
        out.append(
            {
                "name": "What similar apps do",
                "importance": "usually",
                "level": "usually",
                "text": plain(u.note, graph),
                "items": [w["usually_evidence"].get(u.evidence, u.evidence)],
                "shape": "",
            }
        )
    for c in s.could:
        items = [f"When: {plain(c.when, graph)}", f"Because: {plain(c.basis, graph)}"]
        if c.reduced_motion_alt:
            items.append(f"With reduced motion: {plain(c.reduced_motion_alt, graph)}")
        out.append(
            {
                "name": humanize(c.kind),
                "importance": "could",
                "level": "could",
                "text": plain(c.effect, graph),
                "items": items,
                "shape": "",
            }
        )
    return out


def _step_text(graph: SpecGraph, verb: str, target: str | None) -> str:
    tmpl = words()["step_verbs"].get(verb, verb)
    if verb == "go":
        t = graph.screens.get(target or "")
        name = t.name if t else name_of(graph, target or "")
    elif verb == "call":
        op = graph.operations.get(target or "")
        name = op.name.lower() if op else name_of(graph, target or "")
    else:
        name = plain(target or "", graph)
    return tmpl.format(target=name)


def _action(graph: SpecGraph, a: Action) -> dict[str, Any]:
    known = vocab.intent(a.intent)
    effect = [_step_text(graph, st.verb, st.target) for st in a.effect]
    return {
        "label": humanize(a.key),
        "intent": known.what if known is not None else humanize(a.intent).lower(),
        "effect": "; then ".join(effect) if effect else "nothing else happens",
        "importance": a.importance,
        "when": plain(a.precondition, graph),
    }


def _fields_text(rec: dict[str, Any], keep: list[str] | None) -> str:
    fields = rec.get("fields", {})
    keys = [k for k in sorted(fields) if not keep or k in keep] or sorted(fields)
    return "; ".join(f"{humanize(k).lower()}: {_when(str(fields[k]))}" for k in keys[:6])


def _hero(graph: SpecGraph, s: Screen, profile: str, mocks: dict[str, dict[str, Any]]) -> list[str]:
    bound = bound_fields(s)
    ops = bound.pop("<ops>", [])
    prof = mocks.get(profile) or mocks.get("typical") or {}
    out: list[str] = []
    if profile == "broken":
        inj = [i for i in prof.get("injections", []) if i.get("operation") in ops] or prof.get(
            "injections", []
        )[:3]
        for i in inj[:4]:
            op = graph.operations.get(i.get("operation", ""))
            what = op.name.lower() if op else "a request"
            out.append(
                f"Asking the server to {what} fails: {str(i.get('error', '')).replace('_', ' ')} (then: {str(i.get('recovery', '')).replace('_', ' ')})."
            )
        return out
    if profile == "offline":
        out.append("There is no network. The screen shows what was saved last.")
        for eid in sorted(bound):
            c = prof.get("cache", {}).get(eid)
            if c:
                out.append(f"{name_of(graph, eid)}: {len(c.get('row_ids', []))} saved records.")
        return out
    ents = prof.get("entities", {})
    for eid in sorted(bound):
        e = graph.entities.get(eid)
        en = e.name if e else name_of(graph, eid)
        block = ents.get(eid, {})
        rows = block.get("rows", [])
        if profile == "empty" or not rows:
            out.append(f"No {en.lower()} records yet: a brand new account.")
            continue
        for rec in rows[:3]:
            st = f" ({str(rec['state']).replace('_', ' ')})" if rec.get("state") else ""
            out.append(f"{en}{st}: {_fields_text(rec, bound[eid])}")
        if block.get("count", 0) > 3:
            out.append(f"... and {block['count'] - 3} more {en.lower()} records.")
    return out


def screen_view(
    graph: SpecGraph,
    sid: str,
    profile: str = "typical",
    *,
    mock: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The screen shape the Bench reads. ``mock`` is a cache of ``mock_profiles(graph)`` (build once for many screens)."""
    s = graph.screens[sid]
    mocks = mock if mock is not None else mock_profiles(graph)
    prof = profile if profile in mocks else "typical"
    regions = [_region(graph, r) for r in s.regions] + _extra_regions(graph, s)
    regions.sort(key=lambda r: (r["name"].lower(), r["text"]))
    src = graph.locale_copy.get(graph.meta.source_language, {})
    copy = [{"key": k, "text": src[k]} for k in sorted(s.copy_keys) if k in src]
    states = [
        {"name": humanize(st.kind.split(":", 1)[-1]), "text": plain(st.message, graph)}
        for st in sorted(s.states, key=lambda x: x.kind)
    ]
    return {
        "id": s.id,
        "title": s.name,
        "purpose": plain(s.purpose, graph),
        "regions": regions,
        "actions": sorted((_action(graph, a) for a in s.actions), key=lambda a: a["label"].lower()),
        "states": states,
        "copy": copy,
        "copy_missing": len([k for k in s.copy_keys if k not in src]),
        "hero_records": _hero(graph, s, prof, mocks),
        "acceptance": [
            {
                "given": plain(a.given, graph),
                "when": plain(a.when, graph),
                "then": plain(a.then, graph),
            }
            for a in s.acceptance
        ],
        "mock_profile": prof,
        "mock_profile_note": words()["mock_profile_words"].get(prof, ""),
        "roles": [name_of(graph, r) for r in s.role_ids],
        "app": name_of(graph, s.app_id),
        "voices": join_words([words()["voices"][v] for v in ("must", "usually", "could")]),
    }
