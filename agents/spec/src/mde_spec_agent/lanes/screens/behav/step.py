"""The `behav` step (doc 18 1.2 step 12): ONLY the app-specific behaviours. The categories of
`vocab.behavior_categories_for(profile, facts)` whose `supplied_by` is app_specific or mixed are written by one call; the
library supplies the rest (`rcode.instantiate_behaviors`). A behaviour node carries provenance and is `assumed` when
nothing in the data grounds it."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from mde_spec_agent.lanes.screens.batching import run_split
from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, CatIn, IntegIn
from mde_spec_agent.lanes.screens.behav.spec import (
    CALL_ID,
    SPEC,
    VERSION,
    out_schema_version,
    prompt_version,
)
from mde_spec_agent.lanes.screens.behav.validate import repair_basis
from mde_spec_agent.lanes.screens.common import (
    Gate1,
    call_versions,
    degraded_flag,
    facts_text,
    glossary_block,
    log_rcode,
)
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch, node_patch
from mde_spec_core import rcode, vocab
from mde_spec_core.graph import Behavior, Prov, SpecGraph

_NUM = re.compile(r"^BHV-S(\d+)$")


def wanted_categories(graph: SpecGraph, facts: set[str]) -> list[vocab.BehaviorCategory]:
    return [
        c
        for c in vocab.behavior_categories_for(graph.meta.profile, facts)
        if c.supplied_by in ("app_specific", "mixed")
    ]


def behaviour_nodes(
    snap: SpecGraph, out: BehavOut, *, flagged: bool, cats: dict[str, vocab.BehaviorCategory]
) -> list[Behavior]:
    existing: dict[str, Behavior] = {}
    for _, b in sorted(snap.behaviors.items()):
        existing.setdefault(b.category, b)
    top = max([int(m.group(1)) for k in snap.behaviors if (m := _NUM.match(k))] + [0])
    nodes: list[Behavior] = []
    for beh in sorted(out.behaviors, key=lambda b: b.category):
        cat = cats.get(beh.category)
        if cat is None:
            continue
        old = existing.get(beh.category)
        if old is not None:
            bid = old.id
        else:
            top += 1
            bid = f"BHV-S{top:03d}"
        prov = (
            [Prov(kind="library", ref=f"behavior.{cat.name}")] if cat.supplied_by == "mixed" else []
        )
        prov.append(Prov(kind="model", ref=CALL_ID))
        grounded: list[Prov] = []
        shaky = False
        for ref in beh.basis:
            node = snap.find(ref)
            grounded.append(Prov(kind="compass", ref=ref))
            shaky = shaky or bool(node and node.assumed)
        nodes.append(
            Behavior(
                id=bid,
                category=beh.category,
                text=" ".join(beh.text.split()),
                applies_to=sorted(beh.basis),
                assumed=beh.assumed or not beh.basis or shaky or flagged,
                degraded=flagged,
                provenance=[*prov, *grounded],
            )
        )
    return nodes


async def behav_step(ctx: RunContext) -> None:
    cfg = ctx.config
    snap = ctx.graph.model_copy(deep=True)
    facts_set = set(snap.meta.facts) | set(ctx.facts)
    cats = wanted_categories(snap, facts_set)
    if not cats or ctx.runner is None:  # code-only: the library supplies what it can
        rcode.instantiate_behaviors(ctx.graph, facts_set, ctx.rcode)
        return
    by_name = {c.name: c for c in cats}
    first: dict[str, Behavior] = {}
    for _, b in sorted(snap.behaviors.items()):
        first.setdefault(b.category, b)
    categories = [
        CatIn(
            name=c.name,
            what=c.what,
            supplied_by=c.supplied_by,
            library_text=c.default_text,
            existing_id=first[c.name].id if c.name in first else None,
            existing_text=first[c.name].text if c.name in first else "",
        )
        for c in cats
    ]
    integ = [
        IntegIn(
            id=i.id,
            name=i.name,
            provider_side=i.provider_side_flow,
            flow=i.flow,
            interruption=i.interruption,
            pending_timeout=i.pending_timeout,
            events=list(i.events),
        )
        for _, i in sorted(snap.integrations.items())
    ]
    rules = [
        f"{r.id} {r.subject}: {r.statement}"
        for _, r in sorted(snap.rules.items(), key=lambda kv: (not kv[1].confirm, kv[0]))
    ][:12]
    inp0 = BehavIn(
        language=snap.meta.source_language,
        profile=snap.meta.profile,
        facts=facts_text(snap, ctx.spec_input),
        glossary=glossary_block(snap, config=cfg),
        categories=categories,
        integrations=integ,
        roles=[f"{k} {r.name}" for k, r in sorted(snap.roles.items())],
        entities=[
            f"{k} {e.name} ({e.klass})" for k, e in sorted(snap.entities.items()) if not e.later
        ],
        rules=rules,
        languages=list(snap.meta.languages),
    )
    gate1 = Gate1(snap, cfg, ctx.research)

    def gate_one(inp: BehavIn, out: BehavOut) -> list[str]:
        out, _ = repair_basis(inp, out)
        nodes = behaviour_nodes(snap, out, flagged=False, cats=by_name)
        patch = node_patch("behaviors", nodes, replace=True)
        touched = {"behaviors", *[i.id for i in inp.integrations]}
        return gate1.errors(patch, touched, only={"V-35"})

    cs = dataclasses.replace(SPEC, validators=(*SPEC.validators, gate_one))
    versions = call_versions(ctx, CALL_ID, VERSION, prompt_version(), out_schema_version())

    async def produce() -> Patch:
        def make_input(names: list[str]) -> BehavIn:
            return inp0.model_copy(
                update={"categories": [c for c in categories if c.name in names]}
            )

        results = await run_split(
            ctx, cs, [c.name for c in categories], make_input, lambda n: n, "behav"
        )
        nodes: list[Behavior] = []
        flags: list[dict[str, Any]] = []
        rcode: list[dict[str, str]] = []
        for names, res in results:
            repaired, log = repair_basis(make_input(names), res.output)
            rcode += log
            ns = behaviour_nodes(snap, repaired, flagged=res.flagged, cats=by_name)
            nodes += ns
            if res.flagged:
                flags += [degraded_flag("behav", n.id, res.reason or "fallback") for n in ns]
        patch = node_patch("behaviors", nodes, replace=True)
        patch["flags"] = flags
        patch["rcode"] = rcode
        return patch

    patch = await ctx.batch("behav", "behav-000", versions, produce)
    ctx.apply(patch)
    ctx.flags.extend(patch.get("flags", []))
    log_rcode(ctx, patch.get("rcode", []), step="behav")
    rcode.instantiate_behaviors(ctx.graph, facts_set, ctx.rcode)  # a library category still missing
    ctx.emit("notice", what="behav.done", step="behav", categories=len(cats))
