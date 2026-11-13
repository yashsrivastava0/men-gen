"""The patch a SPEC-PLAN answer makes. The answer is applied to a WORKING COPY of the graph with ordinary code (merges and
splits with aliases, module purposes and sharing, role homes, owners, access changes, personas) and the patch is the
difference: every node that changed, plus the ids that are gone (`remove`). The release proposal is not applied to the
graph (the graph stays on the code's release plan); it travels in `plan` for G1.5 together with the consent requests."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from mde_spec_agent.lanes.product.kit.common import (
    ProvAllowed,
    merge_prov,
    prov_nodes,
    tier_and_assumed,
)
from mde_spec_agent.lanes.product.kit.env import Env, input_hash_of
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_agent.run.patch import Patch
from mde_spec_core.graph import AccessRow, Persona, SpecGraph

from .ops import merge_screens, split_screen
from .schema import PlanIn, PlanOut

TABLES = ("modules", "roles", "entities", "screens", "stories", "flows", "personas")


def apply_plan(
    env: Env, inp: PlanIn, out: PlanOut, minter: Minter, flagged: bool
) -> tuple[SpecGraph, dict[str, Any]]:
    """The plan applied to a copy of the snapshot; also the facts about what was merged, split and proposed."""
    work = env.graph.model_copy(deep=True)
    allowed = ProvAllowed.of(inp)
    ih = input_hash_of(inp)
    facts: dict[str, Any] = {
        "aliases": {},
        "merges": [],
        "splits": {},
        "proposal": [],
        "consents": [],
    }

    for m in sorted(out.merges, key=lambda x: (x.keep, x.drop)):
        if m.keep in work.screens and m.drop in work.screens:
            merge_screens(work, m.keep, m.drop, m.reason)
            facts["aliases"][m.drop] = m.keep
            facts["merges"].append({"keep": m.keep, "drop": m.drop, "reason": m.reason})
            work.screens[m.keep].provenance = merge_prov(
                work.screens[m.keep].provenance, prov_nodes(m.prov, env.quote_of)
            )
    by_screen: dict[str, list[Any]] = defaultdict(list)
    for s in out.splits:
        by_screen[s.screen].append(s)
    for sid in sorted(by_screen):
        if sid in work.screens:
            new_ids = split_screen(work, minter, sid, by_screen[sid])
            facts["splits"][sid] = new_ids

    for mr in out.modules:
        node = work.modules.get(mr.id)
        if node is None:
            continue
        node.purpose = node.purpose or mr.purpose
        node.shared = mr.shared
        node.depends_on = sorted(set(mr.depends_on))
        node.provenance = merge_prov(node.provenance, prov_nodes(mr.prov, env.quote_of))
        node.input_hash = ih
    for rr in out.roles:
        role = work.roles.get(rr.id)
        if role is None:
            continue
        role.goal = role.goal or rr.goal
        if role.home_screen is None and rr.home_screen in work.screens:
            role.home_screen = rr.home_screen
        role.provenance = merge_prov(role.provenance, prov_nodes(rr.prov, env.quote_of))
    for orec in out.owners:
        ent = work.entities.get(orec.entity)
        if ent is None or orec.module not in work.modules or ent.owner_module == orec.module:
            continue
        old = ent.owner_module
        if old in work.modules:
            work.modules[old].entity_ids = [i for i in work.modules[old].entity_ids if i != ent.id]
        work.modules[orec.module].entity_ids = sorted(
            {*work.modules[orec.module].entity_ids, ent.id}
        )
        ent.owner_module = orec.module
        ent.provenance = merge_prov(ent.provenance, prov_nodes(orec.prov, env.quote_of))
        ent.input_hash = ih
    for arec in out.access:
        ent = work.entities.get(arec.entity)
        if ent is None:
            continue
        rows = {r.role_id: r for r in ent.access}
        rows[arec.role] = AccessRow(role_id=arec.role, actions=list(arec.actions))  # type: ignore[arg-type]
        ent.access = [rows[k] for k in sorted(rows)]
        ent.input_hash = ih

    by_role = {p.role_id: p for p in work.personas.values()}
    for prec in out.personas:
        prov = prov_nodes(prec.prov, env.quote_of)
        base = by_role.get(prec.role)
        if base is None:
            tier, assumed = tier_and_assumed(prov, allowed.labels)
            pid = minter.mint("PER")
            node = Persona(
                id=pid,
                tier=tier,  # type: ignore[arg-type]
                assumed=assumed,
                provenance=prov,
                role_id=prec.role,
                goal=prec.goal,
            )
        else:
            node = base.model_copy(deep=True)
            node.provenance = merge_prov(node.provenance, prov)
            tier, assumed = tier_and_assumed(node.provenance, allowed.labels)
            node.assumed = assumed
        node.goal = prec.goal
        node.context = prec.context
        node.frequency = prec.frequency
        node.skill = prec.skill
        node.pain_points = list(prec.pain_points)
        node.success = prec.success
        node.input_hash = ih
        node.degraded = node.degraded or flagged
        work.personas[node.id] = node

    base_release = {r.feature: r for r in inp.baseline.release}
    for rec in out.release:
        b = base_release.get(rec.feature)
        changed = b is None or (b.status, b.reason) != (rec.status, rec.reason)
        facts["proposal"].append(
            {
                "feature": rec.feature,
                "status": rec.status,
                "reason": rec.reason,
                "why": rec.why,
                "consent_needed": rec.consent_needed,
                "differs_from_code": changed,
            }
        )
        if rec.consent_needed and changed:
            facts["consents"].append({"feature": rec.feature, "reason": rec.reason, "why": rec.why})
    return work, facts


def build_plan_patch(env: Env, inp: PlanIn, out: PlanOut, minter: Minter, flagged: bool) -> Patch:
    work, facts = apply_plan(env, inp, out, minter, flagged)
    g = env.graph
    tables: dict[str, dict[str, Any]] = {}
    remove: dict[str, list[str]] = {}
    for t in TABLES:
        old: dict[str, Any] = getattr(g, t)
        new: dict[str, Any] = getattr(work, t)
        changed = {
            nid: n.model_dump(mode="json")
            for nid, n in sorted(new.items())
            if nid not in old or old[nid].model_dump(mode="json") != n.model_dump(mode="json")
        }
        if changed:
            tables[t] = changed
        gone = sorted(set(old) - set(new))
        if gone:
            remove[t] = gone
    patch: Patch = {"tables": tables, "replace": True, "plan": facts}
    if remove:
        patch["remove"] = remove
    # copy added by a split (new screens' titles and actions)
    locale_copy = {
        loc: {k: v for k, v in kv.items() if g.locale_copy.get(loc, {}).get(k) != v}
        for loc, kv in work.locale_copy.items()
    }
    locale_copy = {loc: kv for loc, kv in locale_copy.items() if kv}
    if locale_copy:
        patch["locale_copy"] = locale_copy
    return patch
