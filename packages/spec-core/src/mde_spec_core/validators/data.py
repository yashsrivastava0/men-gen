"""V-08..V-11, V-30: roles and access, lifecycles, contract lint, vocabulary across apps, bindings against Contract v0."""

from __future__ import annotations

from mde_spec_core.render import bound_fields

from .base import Context, Finding, validator


@validator(
    "V-08",
    "Every screen and operation has a role rule (default deny); no unused or undefined roles",
)
def v08(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    used: set[str] = set()
    for s in g.screens.values():
        if not s.role_ids:
            out.append(
                Finding(
                    "V-08",
                    "blocker",
                    s.id,
                    "screen has no role rule (default deny)",
                    "name who may see it",
                    "B",
                )
            )
        for r in s.role_ids:
            used.add(r)
            if r not in g.roles:
                out.append(
                    Finding(
                        "V-08",
                        "blocker",
                        s.id,
                        f"role {r} is not defined",
                        "define or remove the role",
                        "B",
                    )
                )
    for o in g.operations.values():
        if not o.roles:
            out.append(
                Finding(
                    "V-08",
                    "blocker",
                    o.id,
                    "operation has no role rule (default deny)",
                    "name who may call it",
                    "B",
                )
            )
        used |= set(o.roles)
        for r in o.roles:
            if r not in g.roles:
                out.append(
                    Finding(
                        "V-08",
                        "blocker",
                        o.id,
                        f"role {r} is not defined",
                        "define or remove the role",
                        "B",
                    )
                )
    for e in g.entities.values():
        for row in e.access:
            if row.role_id not in g.roles:
                out.append(
                    Finding(
                        "V-08",
                        "blocker",
                        e.id,
                        f"access row names undefined role {row.role_id}",
                        "fix the access row",
                        "B",
                    )
                )
    for rid in sorted(set(g.roles) - used):
        out.append(
            Finding(
                "V-08",
                "warning",
                rid,
                "role is used by no screen or operation",
                "remove it or give it a screen",
                "B",
            )
        )
    return out


@validator(
    "V-09", "Lifecycles: every status reachable, terminal states defined, transitions role-gated"
)
def v09(ctx: Context) -> list[Finding]:
    out = []
    for e in ctx.graph.entities.values():
        lc = e.lifecycle
        if len(lc.states) < 2:
            continue
        init = lc.initial or lc.states[0]
        seen, stack = set(), [init]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack += [t.to_state for t in lc.transitions if t.from_state == cur]
        for st in lc.states:
            if st not in seen:
                out.append(
                    Finding(
                        "V-09",
                        "blocker",
                        e.id,
                        f"status {st} is unreachable",
                        "add a transition into it or remove it",
                        "B",
                    )
                )
        if not any(not any(t.from_state == st for t in lc.transitions) for st in lc.states):
            out.append(
                Finding(
                    "V-09",
                    "blocker",
                    e.id,
                    "no terminal state defined",
                    "define at least one terminal status",
                    "B",
                )
            )
        for t in lc.transitions:
            if t.from_state not in lc.states or t.to_state not in lc.states:
                out.append(
                    Finding(
                        "V-09",
                        "blocker",
                        e.id,
                        f"transition {t.from_state}->{t.to_state} names an unknown status",
                        "fix the transition",
                        "B",
                    )
                )
            elif not t.roles:
                out.append(
                    Finding(
                        "V-09",
                        "warning",
                        e.id,
                        f"transition {t.from_state}->{t.to_state} is not role-gated",
                        "name who may cause it",
                        "B",
                    )
                )
    return out


@validator(
    "V-10",
    "Contract lint: operations well formed, error model consistent, enums come from lifecycles",
)
def v10(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    seen: dict[tuple[str, str], str] = {}
    for o in g.operations.values():
        if not o.path:
            out.append(
                Finding("V-10", "blocker", o.id, "operation has no path", "give it a path", "B")
            )
        key = (o.method, o.path)
        if o.kind == "crud" and key in seen:
            out.append(
                Finding(
                    "V-10",
                    "blocker",
                    o.id,
                    f"{o.method} {o.path} is also {seen[key]}",
                    "paths are unique per method",
                    "B",
                )
            )
        seen[key] = o.id
        if not o.errors:
            out.append(
                Finding(
                    "V-10",
                    "blocker",
                    o.id,
                    "operation has no error model",
                    "add its errors and recovery",
                    "B",
                )
            )
        for e in o.errors:
            if not e.code:
                out.append(
                    Finding("V-10", "blocker", o.id, "an error has no code", "name the error", "B")
                )
        if (
            o.method in ("POST", "PUT", "PATCH")
            and o.kind == "custom"
            and not o.idempotent
            and ctx.profile == "deep"
        ):
            out.append(
                Finding(
                    "V-10",
                    "warning",
                    o.id,
                    "deep contract: a custom mutation without idempotency",
                    "state idempotency",
                    "B",
                )
            )
    for e in g.entities.values():
        for f in e.fields:
            if (
                f.type == "enum"
                and f.key == "status"
                and e.lifecycle.states
                and set(f.enum_values) != set(e.lifecycle.states)
            ):
                out.append(
                    Finding(
                        "V-10",
                        "blocker",
                        e.id,
                        "status enum differs from the lifecycle states",
                        "enums come from lifecycles",
                        "A",
                    )
                )
    return out


def _norm(name: str) -> str:
    n = name.casefold().strip()
    return n[:-1] if n.endswith("s") and len(n) > 3 else n


@validator("V-11", "Vocabulary consistent across apps (entity, role, status names)")
def v11(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    by_name: dict[str, str] = {}
    for e in g.entities.values():
        k = _norm(e.name)
        if k in by_name and by_name[k] != e.id:
            out.append(
                Finding(
                    "V-11",
                    "blocker",
                    e.id,
                    f"entity name {e.name!r} also names {by_name[k]}",
                    "one entity, one owner, one name",
                    "B",
                )
            )
        by_name[k] = e.id
    roles: dict[str, str] = {}
    for r in g.roles.values():
        k = _norm(r.name)
        if k in roles and roles[k] != r.id:
            out.append(
                Finding(
                    "V-11",
                    "warning",
                    r.id,
                    f"role {r.name!r} duplicates {roles[k]}",
                    "merge the duplicate role",
                    "B",
                )
            )
        roles[k] = r.id
    return out


@validator(
    "V-30",
    "Bindings resolve to Contract v0 fields with matching types; every action's operation exists and is role-gated",
    gate=1,
)
def v30(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    for s in g.screens.values():
        bf = bound_fields(s)
        for eid, fields in bf.items():
            if eid == "<ops>":
                continue
            e = g.entities.get(eid)
            if e is None:
                out.append(
                    Finding(
                        "V-30",
                        "blocker",
                        s.id,
                        f"binds missing entity {eid}",
                        "bind an entity of the data model",
                        "B",
                    )
                )
                continue
            have = {f.key for f in e.fields}
            for f in fields:
                if f not in have:
                    out.append(
                        Finding(
                            "V-30",
                            "blocker",
                            s.id,
                            f"{eid}.{f} is not a field",
                            "bind a real field",
                            "B",
                        )
                    )
            # access: a role that may only `none` an entity must not bind it
            for row in e.access:
                if row.role_id in s.role_ids and row.actions == ["none"]:
                    out.append(
                        Finding(
                            "V-30",
                            "blocker",
                            s.id,
                            f"role {row.role_id} may not see {eid}",
                            "narrow the binding to what the role may see (R-ask, else drop and list at G2)",
                            "B",
                        )
                    )
        for oid in bf["<ops>"]:
            op = g.operations.get(oid)
            if op and op.roles and not (set(op.roles) & set(s.role_ids)):
                out.append(
                    Finding(
                        "V-30",
                        "blocker",
                        s.id,
                        f"no role of this screen may call {oid}",
                        "role-gate the operation for this screen's roles",
                        "B",
                    )
                )
    return out
