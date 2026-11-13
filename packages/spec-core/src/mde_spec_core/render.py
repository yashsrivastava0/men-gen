"""Deterministic render of the graph into pack text (doc 15 5.6, 5.7; doc 18 G-1, G-2).

The model never writes pack files: code renders Markdown with a small YAML header and flat lists from the graph.
Bodies are byte-stable: no version, hash, token size, dependents or timestamp, keys in a fixed order, LF endings,
regions and actions listed alphabetically by key (no order, no position carries meaning).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .canon import normalise_text
from .graph import (
    Action,
    Entity,
    Guard,
    Operation,
    Region,
    Screen,
    SpecGraph,
    Step,
)


class TokenCounter:
    """Counts with the consumer's counter, or a conservative factor of characters per token until measured."""

    def __init__(self, chars_per_token: float = 3.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)


class Raw(str):
    """Already-rendered fragment (a list or inline map): never quoted again."""


def _v(value: object) -> str:
    """One scalar for an inline map; commas and braces in text are kept readable and unambiguous."""
    if isinstance(value, Raw):
        return str(value)
    s = str(value).replace("\n", " ").strip()
    if any(c in s for c in ",{}[]:#") or s != str(value):
        return '"' + s.replace('"', "'") + '"'
    return s


def flow(pairs: Iterable[tuple[str, object]]) -> Raw:
    items = [f"{k}: {_v(v)}" for k, v in pairs if v not in (None, "", [], False)]
    return Raw("{" + ", ".join(items) + "}")


def _lst(values: Iterable[object]) -> Raw:
    return Raw("[" + ", ".join(_v(v) for v in values) + "]")


def _guard(g: Guard) -> str:
    if g.pred in ("signed_in", "role_has_access"):
        return g.pred
    return flow(
        [
            ("entity", g.entity),
            ("field", g.field),
            ("equals" if g.pred == "field_equals" else "in", ",".join(g.values)),
        ]
    )


def _step(s: Step) -> str:
    return s.verb if s.target is None else f"{s.verb} {s.target}"


def _region(r: Region) -> str:
    shape = r.shape + (f" {{kind: {r.shape_kind}}}" if r.shape_kind else "")
    return "  - " + flow(
        [
            ("key", r.key),
            ("shape", shape),
            ("use_block", r.use_block),
            ("what", r.what),
            ("bind", _lst(r.bind) if r.bind else None),
            ("importance", r.importance),
            ("cardinality", r.cardinality),
            ("typical", r.typical),
            ("max", r.maximum),
            ("live", None if r.live == "none" else r.live),
            ("freshness", r.freshness),
            ("copy_unspecified", r.copy_unspecified),
        ]
    )


def _action(a: Action) -> str:
    return "  - " + flow(
        [
            ("key", a.key),
            ("importance", a.importance),
            ("intent", a.intent),
            ("what", a.what),
            ("precondition", a.precondition),
            ("effect", _lst(_step(s) for s in a.effect)),
            ("guard", _lst(_guard(g) for g in a.guard) if a.guard else None),
            ("errors", _lst(a.errors) if a.errors else None),
            ("flags", _lst(a.flags) if a.flags else None),
            ("unspecified", a.unspecified),
        ]
    )


def render_card(screen: Screen) -> str:
    """The card: what the screen must communicate and do. Never where or how."""
    L: list[str] = ["---"]
    L.append(f"id: {screen.id}   kind: screen   name: {_v(screen.name)}")
    L.append(
        f"app: {screen.app_id}   module: {screen.module_id}   screen_type: {screen.screen_type}   surface: {screen.surface}"
    )
    L.append(f"roles: {_lst(screen.role_ids)}")
    L.append(
        f"tiers: {flow([('purpose', screen.tier), ('regions', 'default'), ('actions', 'default'), ('states', 'default')])}"
    )
    prov = sorted(f"{p.kind}:{p.ref}" if p.ref else p.kind for p in screen.provenance)
    L.append(f"provenance: {_lst(prov)}")
    if screen.claims_used:
        L.append(f"claims_used: {_lst(sorted(screen.claims_used))}")
    if screen.stories:
        L.append(f"stories: {_lst(sorted(screen.stories))}")
    L.append("---")
    L.append(f"purpose: {screen.purpose}")
    r = screen.route
    if r.path:
        params = _lst(
            flow([("name", p.name), ("type", p.type), ("source", p.source)]) for p in r.params
        )
        L.append(
            f"route: {flow([('name', r.name), ('path', r.path), ('params', params), ('guard', _lst(_guard(g) for g in r.guards) if r.guards else None)])}"
        )
    if screen.reached_from:
        L.append(
            f"reached_from: {_lst(sorted(screen.reached_from))}   leads_to: {_lst(sorted(screen.leads_to))}"
        )
    elif screen.leads_to:
        L.append(f"leads_to: {_lst(sorted(screen.leads_to))}")
    if screen.role_conditions:
        L.append(f"role_conditions: {screen.role_conditions}")
    L.append("regions:   # alphabetical by key; the order carries no meaning")
    L += [_region(x) for x in sorted(screen.regions, key=lambda x: x.key)]
    L.append("actions:")
    L += [_action(x) for x in sorted(screen.actions, key=lambda x: x.key)]
    if screen.inputs:
        L.append("inputs:")
        L += [
            "  - "
            + flow(
                [
                    ("key", i.key),
                    ("type", i.type),
                    ("required", i.required),
                    ("constraint", i.constraint),
                    ("default", i.default),
                    ("source", i.source),
                ]
            )
            for i in sorted(screen.inputs, key=lambda x: x.key)
        ]
    L.append("states:   # what each must communicate, not how")
    L += [
        "  - " + flow([("kind", s.kind), ("message", s.message)])
        for s in sorted(screen.states, key=lambda x: x.kind)
    ]
    L.append(f"copy_keys: {_lst(sorted(screen.copy_keys))}")
    if screen.analytics:
        L.append(f"analytics: {_lst(sorted(screen.analytics))}")
    if screen.accessibility:
        L.append(f"accessibility: {screen.accessibility}")
    L.append("acceptance:")
    L += [f"  - GIVEN {a.given} WHEN {a.when} THEN {a.then}" for a in screen.acceptance]
    if screen.usually:
        L.append("usually:")
        L += [
            "  - " + flow([("note", u.note), ("evidence", u.evidence), ("item", u.item)])
            for u in screen.usually
        ]
    if screen.mock_profiles or screen.hero_refs:
        L.append(
            f"mock: {flow([('profiles', _lst(screen.mock_profiles)), ('hero', _lst(screen.hero_refs))])}"
        )
    if screen.confirm:
        L.append(f"confirm: {_lst(screen.confirm)}")
    return normalise_text("\n".join(L))


def render_could(screen: Screen) -> str:
    """screens/<id>.could.md: ideas, Open, intent and effect only, each with a basis. Outside the G2 hash."""
    L = ["---", f"id: {screen.id}   kind: could   voice: could   tier: open", "---"]
    for c in screen.could:
        L.append(
            flow(
                [
                    ("id", c.id),
                    ("kind", c.kind),
                    ("when", c.when),
                    ("effect", c.effect),
                    ("basis", c.basis),
                    ("assumed", c.assumed),
                    ("motion", c.motion),
                    ("reduced_motion_alt", c.reduced_motion_alt),
                    ("tier", c.tier),
                ]
            )
        )
    return normalise_text("\n".join(L))


# ---------------------------------------------------------------- closure (generated, read-only)


def _field_line(f: object) -> str:
    from .graph import FieldDef

    assert isinstance(f, FieldDef)
    extra = f", enum: {'|'.join(f.enum_values)}" if f.enum_values else ""
    deriv = f", derived: {f.derived}" if f.derived else ""
    return f"{f.key} ({f.type}{extra}{deriv})"


def bound_fields(screen: Screen) -> dict[str, list[str]]:
    """ENT-x -> field keys the screen binds; OP-x listed under key '<ops>'."""
    ents: dict[str, list[str]] = {}
    ops: list[str] = []
    for r in screen.regions:
        for b in r.bind:
            if b.startswith("OP-"):
                ops.append(b)
            elif "." in b:
                e, f = b.split(".", 1)
                ents.setdefault(e, [])
                if f not in ents[e]:
                    ents[e].append(f)
    for a in screen.actions:
        for s in a.effect:
            if s.verb == "call" and s.target:
                ops.append(s.target)
    ents["<ops>"] = sorted(set(ops))
    return ents


def render_closure(screen: Screen, graph: SpecGraph, blocks: dict[str, str] | None = None) -> str:
    """Everything the screen needs to be built alone (G-2): fields used with lifecycle states, operations with errors
    and recovery, access rows, source copy, route targets of its navigation effects, block bodies."""
    L = ["## closure"]
    b = bound_fields(screen)
    ops = b.pop("<ops>")
    for eid in sorted(b):
        ent = graph.entities.get(eid)
        if ent is None:
            L.append(f"{eid} (unresolved)")
            continue
        by_key = {f.key: f for f in ent.fields}
        used = [by_key[k] for k in b[eid] if k in by_key]
        line = f"{eid} {ent.name}, fields used: " + "; ".join(_field_line(f) for f in used)
        if any(f.enum_values for f in used) or ent.lifecycle.transitions:
            line += f"; lifecycle: {'|'.join(ent.lifecycle.states)}"
        L.append(line)
    for oid in ops:
        op = graph.operations.get(oid)
        if op is None:
            L.append(f"{oid} (unresolved)")
            continue
        L.append(_op_line(op))
    if ops:
        rec = sorted(
            {
                f"{e.code} -> {e.recovery or 'stay'}"
                for o in ops
                if (op := graph.operations.get(o))
                for e in op.errors
            }
        )
        if rec:
            L.append("error recovery: " + "; ".join(rec))
    acc: list[str] = []
    for eid in sorted(b):
        ent = graph.entities.get(eid)
        if ent:
            for row in ent.access:
                if row.role_id in screen.role_ids:
                    acc.append(f"{row.role_id} -> {ent.name}: {'/'.join(row.actions)}")
    if acc:
        L.append("access (this screen): " + "; ".join(sorted(set(acc))))
    # route targets of navigation effects (route name, typed parameters, guard)
    targets = sorted(
        {s.target for a in screen.actions for s in a.effect if s.verb == "go" and s.target}
        | set(screen.leads_to)
    )
    for t in targets:
        tgt = graph.screens.get(t)
        if tgt is None:
            L.append(f"route {t} (unresolved)")
            continue
        p = ", ".join(f"{x.name}: {x.type}" for x in tgt.route.params)
        gd = ", ".join(_guard(x) for x in tgt.route.guards)
        L.append(
            f"route {t}: {tgt.route.name or tgt.id.lower()} {tgt.route.path} ({p}){' guard: ' + gd if gd else ''}"
        )
    lang = graph.meta.source_language
    texts = graph.locale_copy.get(lang, {})
    L.append(
        "copy ("
        + lang
        + "): "
        + "; ".join(f"{k} {texts[k]!r}" for k in sorted(screen.copy_keys) if k in texts)
    )
    if blocks:
        for r in sorted(screen.regions, key=lambda x: x.key):
            if r.use_block:
                bid = r.use_block.split("@")[0]
                if bid in blocks:
                    L.append(f"block {r.use_block}: {blocks[bid]}")
    L.append("tokens: names only; none used by value")
    return normalise_text("\n".join(L))


def _op_line(op: Operation) -> str:
    req = ", ".join(_field_line(f) for f in op.request)
    res = ", ".join(_field_line(f) for f in op.response)
    errs = ", ".join(e.code for e in op.errors)
    flags = " idempotent" if op.idempotent else ""
    return f"{op.id} {op.name} {op.method} {op.path}({req}) -> ({res}); errors: {errs}{flags}"


def render_slice(screen: Screen, graph: SpecGraph, blocks: dict[str, str] | None = None) -> str:
    """screens/<id>.md = the card with its generated closure below it."""
    return render_card(screen) + "\n" + render_closure(screen, graph, blocks)


# ---------------------------------------------------------------- global files (topic by topic)


def render_entity(ent: Entity) -> str:
    L = [
        f"## {ent.id} {ent.name}   owner: {ent.owner_module}   class: {ent.klass}"
        + ("   later" if ent.later else "")
    ]
    L.append(
        "fields: " + "; ".join(_field_line(f) + ("*" if f.required else "") for f in ent.fields)
    )
    if ent.lifecycle.states:
        tr = ", ".join(f"{t.from_state}->{t.to_state}" for t in ent.lifecycle.transitions)
        L.append(f"lifecycle: {'|'.join(ent.lifecycle.states)}; transitions: {tr}")
    if ent.access:
        L.append(
            "access: "
            + "; ".join(
                f"{a.role_id}: {'/'.join(a.actions)}"
                for a in sorted(ent.access, key=lambda x: x.role_id)
            )
        )
    if ent.typical_count or ent.max_count:
        L.append(f"volumes: typical {ent.typical_count}, max {ent.max_count}")
    return "\n".join(L)
