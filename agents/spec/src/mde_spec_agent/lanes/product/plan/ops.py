"""Merging and splitting screens on a WORKING COPY of the graph (code: deterministic, no model text is ever executed).

`merge_screens`: `drop` is folded into `keep`: regions, actions, inputs, states, copy keys, acceptance and stories join `keep`
(a clashing key gets a numbered suffix), every reference to `drop` follows `keep`, and `drop`'s id (and its own aliases)
become aliases of `keep`. `split_screen`: part 1 keeps the original id and the rest; each further part is a new screen with
the regions and actions the plan moved to it, its own copy keys, a link from the original and a link back, and it cites the
stories of the original. A step `go` to the screen itself becomes `stay`."""

from __future__ import annotations

from collections.abc import Sequence

from mde_spec_agent.derive.text import slug
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_core.graph import Prov, Route, Screen, SpecGraph
from mde_spec_core.ids import namespaced_copy_key

from .schema import SplitRec


def _unique(key: str, taken: set[str]) -> str:
    if key not in taken:
        return key
    n = 2
    while f"{key}_{n}" in taken:
        n += 1
    return f"{key}_{n}"


def _retarget(work: SpecGraph, old: str, new: str) -> None:
    """Every reference to screen `old` now names `new` (a link to itself disappears)."""
    for s in work.screens.values():
        s.leads_to = [
            x for x in dict.fromkeys(new if t == old else t for t in s.leads_to) if x != s.id
        ]
        s.reached_from = list(
            dict.fromkeys(
                (new + t[len(old) :]) if (t == old or t.startswith(old + ".")) else t
                for t in s.reached_from
            )
        )
        for a in s.actions:
            for st in a.effect:
                if st.target == old:
                    st.target = new
                if s.id == new and st.verb == "go" and st.target == new:
                    st.verb, st.target = "stay", None
    for m in work.modules.values():
        m.screen_ids = list(dict.fromkeys(new if i == old else i for i in m.screen_ids))
    for st in work.stories.values():
        st.screen_ids = list(dict.fromkeys(new if i == old else i for i in st.screen_ids))
    for fl in work.flows.values():
        for stg in fl.stages:
            stg.screen_ids = list(dict.fromkeys(new if i == old else i for i in stg.screen_ids))
    for r in work.roles.values():
        if r.home_screen == old:
            r.home_screen = new


def merge_screens(work: SpecGraph, keep: str, drop: str, reason: str) -> None:
    a, b = work.screens[keep], work.screens[drop]
    rkeys = {r.key for r in a.regions}
    for r in b.regions:
        rr = r.model_copy(deep=True)
        rr.key = _unique(rr.key, rkeys)
        rkeys.add(rr.key)
        a.regions.append(rr)
    akeys = {x.key for x in a.actions}
    for act in b.actions:
        aa = act.model_copy(deep=True)
        aa.key = _unique(aa.key, akeys)
        akeys.add(aa.key)
        a.actions.append(aa)
    ikeys = {i.key for i in a.inputs}
    a.inputs += [i.model_copy(deep=True) for i in b.inputs if i.key not in ikeys]
    kinds = {s.kind for s in a.states}
    a.states += [s.model_copy(deep=True) for s in b.states if s.kind not in kinds]
    a.copy_keys = sorted({*a.copy_keys, *b.copy_keys})
    seen = {(x.given, x.when, x.then) for x in a.acceptance}
    a.acceptance += [
        x.model_copy(deep=True) for x in b.acceptance if (x.given, x.when, x.then) not in seen
    ]
    a.stories = sorted({*a.stories, *b.stories})
    a.role_ids = sorted({*a.role_ids, *b.role_ids})
    a.claims_used = sorted({*a.claims_used, *b.claims_used})
    a.confirm = sorted({*a.confirm, *b.confirm})
    a.leads_to = [x for x in dict.fromkeys([*a.leads_to, *b.leads_to]) if x not in (keep, drop)]
    a.reached_from = list(dict.fromkeys([*a.reached_from, *b.reached_from]))
    a.aliases = sorted({*a.aliases, drop, *b.aliases})
    seenp = {(p.kind, p.ref) for p in a.provenance}
    a.provenance += [p for p in b.provenance if (p.kind, p.ref) not in seenp]
    del work.screens[drop]
    _retarget(work, drop, keep)
    work.screens[keep].regions.sort(key=lambda r: r.key)
    work.screens[keep].actions.sort(key=lambda x: x.key)


def split_screen(
    work: SpecGraph, minter: Minter, screen_id: str, parts: Sequence[SplitRec]
) -> list[str]:
    """Part 1 keeps `screen_id`; every other part becomes a new screen. Returns the new ids."""
    s = work.screens[screen_id]
    ordered = sorted(parts, key=lambda p: p.part)
    new_ids: list[str] = []
    for p in ordered[1:]:
        nid = minter.mint("SCR")
        regions = [r.model_copy(deep=True) for r in s.regions if r.key in set(p.region_keys)]
        actions = [x.model_copy(deep=True) for x in s.actions if x.key in set(p.action_keys)]
        prefix = namespaced_copy_key(screen_id, "")
        shared = [
            k for k in s.copy_keys if not k.startswith(prefix)
        ]  # library state keys every screen of a type shares
        copy = {
            namespaced_copy_key(nid, "title"): p.name,
            **{namespaced_copy_key(nid, x.key): x.what for x in actions},
        }
        new = Screen(
            id=nid,
            tier="default",
            assumed=True,
            provenance=[
                Prov(
                    kind="assumed",
                    ref=f"split of {screen_id}: {p.reason or 'over the size cap'}"[:160],
                )
            ],
            name=p.name,
            app_id=s.app_id,
            module_id=s.module_id,
            screen_type=s.screen_type,
            surface=s.surface,
            role_ids=list(s.role_ids),
            purpose=p.purpose,
            route=Route(name=slug(p.name), path=f"/{slug(p.name)}"),
            reached_from=[screen_id],
            leads_to=[screen_id],
            regions=regions,
            actions=actions,
            states=[x.model_copy(deep=True) for x in s.states],
            copy_keys=sorted({*copy, *shared}),
            acceptance=[x.model_copy(deep=True) for x in s.acceptance],
            stories=list(s.stories),
            system=False,
        )
        for loc in work.meta.languages or [work.meta.source_language]:
            work.locale_copy.setdefault(loc, {}).update(copy)
        moved_r = {r.key for r in regions}
        moved_a = {x.key for x in actions}
        s.regions = [r for r in s.regions if r.key not in moved_r]
        s.actions = [x for x in s.actions if x.key not in moved_a]
        s.leads_to = list(dict.fromkeys([*s.leads_to, nid]))
        work.screens[nid] = new
        new_ids.append(nid)
        if s.module_id and s.module_id in work.modules:
            m = work.modules[s.module_id]
            m.screen_ids = list(dict.fromkeys([*m.screen_ids, nid]))
        for st in work.stories.values():
            if screen_id in st.screen_ids:
                st.screen_ids = list(dict.fromkeys([*st.screen_ids, nid]))
        for fl in (
            work.flows.values()
        ):  # a stage that uses an action which moved now touches the new screen too
            for stg in fl.stages:
                if screen_id in stg.screen_ids and moved_a & set(stg.action_keys):
                    stg.screen_ids = list(dict.fromkeys([*stg.screen_ids, nid]))
    return new_ids
