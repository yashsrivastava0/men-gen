"""V-01..V-07, V-31, V-32: schema, ids, edges, reachability, flows, bindings, action targets, keys, namespaces."""

from __future__ import annotations

from mde_spec_core.graph import NODE_TYPES, Screen, SpecGraph
from mde_spec_core.ids import is_valid_key, parse_id

from .base import Context, Finding, validator

_KIND_BY_TABLE = {
    "features": "FTR",
    "roles": "ROL",
    "apps": "APP",
    "modules": "MOD",
    "entities": "ENT",
    "operations": "OP",
    "rules": "RUL",
    "integrations": "INT",
    "screens": "SCR",
    "personas": "PER",
    "stories": "US",
    "flows": "FLW",
    "behaviors": "BHV",
    "directions": "DIR",
    "assets": "ASSET",
}
_TERMINAL_TYPES = {"confirmation", "legal", "status", "permission"}


def _all_ids(g: SpecGraph) -> set[str]:
    ids: set[str] = set()
    for kind in NODE_TYPES:
        ids |= set(getattr(g, kind))
    return ids | set(g.hero)


def _targets(s: Screen) -> set[str]:
    out = set(s.leads_to)
    for a in s.actions:
        out |= {st.target for st in a.effect if st.verb == "go" and st.target}
    return {t for t in out if t.startswith("SCR-")}


@validator("V-01", "Every artifact validates against its versioned schema", gate=1)
def v01(ctx: Context) -> list[Finding]:
    from pydantic import ValidationError

    try:
        SpecGraph.model_validate(ctx.graph.model_dump())
    except ValidationError as e:
        return [
            Finding(
                "V-01",
                "blocker",
                "graph",
                str(e.errors()[0]["msg"]),
                "fix the node that fails its schema",
                "B",
            )
        ]
    return []


@validator("V-02", "IDs unique and stable; tombstones and aliases resolve", gate=1)
def v02(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    seen: dict[str, str] = {}
    for table in NODE_TYPES:
        for nid in getattr(g, table):
            if nid in seen:
                out.append(
                    Finding(
                        "V-02",
                        "blocker",
                        nid,
                        f"id used in {seen[nid]} and {table}",
                        "a duplicate id is never fixed by code: re-ask the owning call",
                        "B",
                    )
                )
            seen[nid] = table
            p = parse_id(nid)
            if p is None:
                out.append(
                    Finding(
                        "V-02",
                        "blocker",
                        nid,
                        "not a valid id (KIND-123 or KIND-S123)",
                        "mint a valid id",
                        "A",
                    )
                )
    ids = set(seen)
    for n in g.all_nodes():
        for al in n.aliases:
            if al in ids and al != n.id:
                out.append(
                    Finding(
                        "V-02",
                        "blocker",
                        n.id,
                        f"alias {al} is also a live id",
                        "an alias must never be reused as an id",
                        "B",
                    )
                )
    return out


@validator("V-03", "Every edge points to an existing node", gate=1)
def v03(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    ids = _all_ids(g)

    def chk(src: str, field_: str, refs: list[str | None]) -> None:
        for r in refs:
            if r and r not in ids:
                out.append(
                    Finding(
                        "V-03",
                        "blocker",
                        src,
                        f"{field_} points to missing {r}",
                        "add the node or drop the edge",
                        "B",
                    )
                )

    for a in g.apps.values():
        chk(a.id, "role_ids", list(a.role_ids))
        chk(a.id, "module_ids", list(a.module_ids))
    for m in g.modules.values():
        chk(m.id, "screen_ids", list(m.screen_ids))
        chk(m.id, "entity_ids", list(m.entity_ids))
    for s in g.screens.values():
        chk(s.id, "app_id", [s.app_id])
        chk(s.id, "module_id", [s.module_id])
        chk(s.id, "role_ids", list(s.role_ids))
        chk(s.id, "leads_to", list(s.leads_to))
        chk(s.id, "stories", list(s.stories))
    for e in g.entities.values():
        chk(e.id, "owner_module", [e.owner_module])
    for o in g.operations.values():
        chk(o.id, "entity", [o.entity])
    for st in g.stories.values():
        chk(st.id, "screen_ids", list(st.screen_ids))
        chk(st.id, "feature_ids", list(st.feature_ids))
    for f in g.flows.values():
        for stg in f.stages:
            chk(f.id, "stage screens", list(stg.screen_ids))
    for r in g.roles.values():
        chk(r.id, "home_screen", [r.home_screen])
    return out


def entry_screens(g: SpecGraph) -> set[str]:
    """Entry points: role homes, auth and onboarding screens, and the first screen of each app."""
    entries = {r.home_screen for r in g.roles.values() if r.home_screen}
    entries |= {s.id for s in g.screens.values() if s.screen_type in ("auth", "onboarding")}
    for app_id in g.apps:
        first = sorted(s.id for s in g.screens.values() if s.app_id == app_id and s.status == "in")
        if first:
            entries.add(first[0])
    return {e for e in entries if e}


@validator(
    "V-04", "Every screen is reachable from an entry point; no dead ends except terminal screens"
)
def v04(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    live = {sid: s for sid, s in g.screens.items() if s.status == "in"}
    seen: set[str] = set()
    stack = [e for e in entry_screens(g) if e in live]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(t for t in _targets(live[cur]) if t in live)
    for sid in sorted(set(live) - seen):
        out.append(
            Finding(
                "V-04",
                "blocker",
                sid,
                "not reachable from any entry point",
                "add an entry or a link that leads here, or remove the screen",
                "B",
            )
        )
    for sid, s in sorted(live.items()):
        if not _targets(s) and s.screen_type not in _TERMINAL_TYPES:
            out.append(
                Finding(
                    "V-04",
                    "warning",
                    sid,
                    "dead end: no exit and not a terminal screen type",
                    "declare an exit",
                    "B",
                )
            )
    return out


@validator("V-05", "Every flow starts and ends on real screens; every step maps to an action")
def v05(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    for f in g.flows.values():
        if not f.stages:
            out.append(
                Finding("V-05", "blocker", f.id, "flow has no stages", "write the stages", "B")
            )
            continue
        for ends in (f.stages[0], f.stages[-1]):
            if not ends.screen_ids:
                out.append(
                    Finding(
                        "V-05",
                        "blocker",
                        f.id,
                        f"stage {ends.n} has no screen",
                        "a flow starts and ends on real screens",
                        "B",
                    )
                )
        for stg in f.stages:
            keys = {a.key for sid in stg.screen_ids if (s := g.screens.get(sid)) for a in s.actions}
            for ak in stg.action_keys:
                if ak not in keys:
                    out.append(
                        Finding(
                            "V-05",
                            "blocker",
                            f.id,
                            f"stage {stg.n} names action {ak} that its screens do not have",
                            "map the step to a real action",
                            "B",
                        )
                    )
    return out


def _op_or_field(g: SpecGraph, ref: str) -> bool:
    if ref.startswith("OP-"):
        return ref in g.operations
    if "." in ref:
        eid, fkey = ref.split(".", 1)
        e = g.entities.get(eid)
        return bool(e and any(f.key == fkey for f in e.fields))
    return False


@validator("V-06", "Every data binding names a real entity field or operation", gate=1)
def v06(ctx: Context) -> list[Finding]:
    out = []
    for s in ctx.graph.screens.values():
        for r in s.regions:
            for b in r.bind:
                if not _op_or_field(ctx.graph, b):
                    out.append(
                        Finding(
                            "V-06",
                            "blocker",
                            f"{s.id}.{r.key}",
                            f"binding {b} names no real field or operation",
                            "bind a field of Contract v0 or drop the region binding",
                            "B",
                        )
                    )
    return out


@validator(
    "V-07", "Every action resolves to a navigation target, mutation or flow that exists", gate=1
)
def v07(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    flows = (
        {i.need_key for i in g.integrations.values()}
        | {i.id for i in g.integrations.values()}
        | {i.name for i in g.integrations.values()}
    )
    for s in g.screens.values():
        for a in s.actions:
            if a.unspecified:
                continue  # listed at G2 as needs_decision (FN-36)
            if len(a.effect) > 3:
                out.append(
                    Finding(
                        "V-07",
                        "blocker",
                        f"{s.id}.{a.key}",
                        "effect has more than three steps",
                        "keep at most three typed steps",
                        "B",
                    )
                )
            for st in a.effect:
                if st.verb == "go" and (
                    st.target not in g.screens or g.screens[st.target or ""].status != "in"
                ):
                    out.append(
                        Finding(
                            "V-07",
                            "blocker",
                            f"{s.id}.{a.key}",
                            f"go {st.target} is not a live screen",
                            "point to a screen that exists",
                            "B",
                        )
                    )
                elif st.verb == "call" and st.target not in g.operations:
                    out.append(
                        Finding(
                            "V-07",
                            "blocker",
                            f"{s.id}.{a.key}",
                            f"call {st.target} is not an operation",
                            "name an operation of Contract v0 (one additive re-ask to Product)",
                            "B",
                        )
                    )
                elif st.verb == "external_flow" and st.target not in flows:
                    out.append(
                        Finding(
                            "V-07",
                            "blocker",
                            f"{s.id}.{a.key}",
                            f"external flow {st.target} has no integration record",
                            "add the integration need",
                            "B",
                        )
                    )
    return out


@validator("V-31", "Region and action keys unique per screen (they become testIDs)", gate=1)
def v31(ctx: Context) -> list[Finding]:
    out = []
    for s in ctx.graph.screens.values():
        for what, keys in (
            ("region", [r.key for r in s.regions]),
            ("action", [a.key for a in s.actions]),
            ("input", [i.key for i in s.inputs]),
        ):
            for k in sorted({k for k in keys if keys.count(k) > 1}):
                out.append(
                    Finding(
                        "V-31",
                        "blocker",
                        s.id,
                        f"duplicate {what} key {k}",
                        "keys are minted once: re-ask the owning card",
                        "B",
                    )
                )
            for k in keys:
                if not is_valid_key(k):
                    out.append(
                        Finding(
                            "V-31",
                            "blocker",
                            s.id,
                            f"{what} key {k!r} is not lower snake case",
                            "rename (keep the old key as an alias)",
                            "A",
                        )
                    )
    return out


@validator(
    "V-32",
    "ID namespace and aliases: Spec-minted ids in their namespace; every alias resolves; no reuse",
    gate=1,
)
def v32(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    live = _all_ids(g)
    seen_alias: dict[str, str] = {}
    for n in g.all_nodes():
        for al in n.aliases:
            if al in seen_alias and seen_alias[al] != n.id:
                out.append(
                    Finding(
                        "V-32",
                        "blocker",
                        n.id,
                        f"alias {al} also claimed by {seen_alias[al]}",
                        "an alias resolves to exactly one node",
                        "B",
                    )
                )
            seen_alias[al] = n.id
            if parse_id(al) is None:
                out.append(
                    Finding(
                        "V-32",
                        "blocker",
                        n.id,
                        f"alias {al} is not a valid id",
                        "fix the alias",
                        "A",
                    )
                )
    for s in g.screens.values():
        for t in s.leads_to + s.reached_from:
            base = t.split(".")[0]
            if base.startswith("SCR-") and base not in live and base not in seen_alias:
                out.append(
                    Finding(
                        "V-32",
                        "warning",
                        s.id,
                        f"reference {t} resolves to nothing, not even an alias",
                        "add the alias or fix the reference",
                        "B",
                    )
                )
    return out
