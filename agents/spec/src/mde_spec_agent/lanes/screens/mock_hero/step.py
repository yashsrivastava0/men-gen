"""The `hero` step (doc 18 1.2 step 11), parallel with `screens` after the Freeze.

Code mints the skeleton at the Freeze (if the graph has none yet this step mints it first and stores it as a checkpointed
patch); the model FILLS the content of every record, related entities in the SAME call (`hero.fill_plan`). The fill is
applied with `hero.apply_fill`, which rejects unsafe values; a value that appears in two entities is DERIVED by code from the
parent record (`derive_shared_values`), never written twice. Patches are applied in sorted group order."""

from __future__ import annotations

import dataclasses
import random
from typing import Any

from mde_spec_agent.calls.base import RunResult
from mde_spec_agent.lanes.screens.batching import gather_batches, run_split
from mde_spec_agent.lanes.screens.common import (
    call_versions,
    degraded_flag,
    facts_text,
    glossary_block,
)
from mde_spec_agent.lanes.screens.mock_hero.generate import brief_text, pool_of
from mde_spec_agent.lanes.screens.mock_hero.schema import (
    FieldRuleIn,
    HeroEntityIn,
    HeroIn,
    HeroOut,
    RecIn,
)
from mde_spec_agent.lanes.screens.mock_hero.spec import (
    CALL_ID,
    SPEC,
    VERSION,
    out_schema_version,
    prompt_version,
)
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.graph import SpecGraph
from mde_spec_core.hero import (
    FillGroup,
    apply_fill,
    derive_shared_values,
    fill_plan,
    mint_hero_skeleton,
)


def split_group(batch: list[FillGroup]) -> list[list[FillGroup]]:
    """A truncated group splits back into its parts: entities in halves, then one entity's records in halves."""
    if len(batch) != 1:
        mid = (len(batch) + 1) // 2
        return [batch[:mid], batch[mid:]]
    g = batch[0]
    if len(g.entities) > 1:
        mid = (len(g.entities) + 1) // 2
        out = []
        for part in (g.entities[:mid], g.entities[mid:]):
            out.append(
                [
                    dataclasses.replace(
                        g,
                        id=f"{g.id}.{len(out) + 1}",
                        entities=list(part),
                        record_ids={e: g.record_ids[e] for e in part},
                        write_fields={e: g.write_fields[e] for e in part},
                        code_set={e: g.code_set[e] for e in part},
                        derived={e: g.derived[e] for e in part},
                        image_brief_for={e: g.image_brief_for[e] for e in part},
                        split_from_larger=True,
                    )
                ]
            )
        return out
    eid = g.entities[0]
    recs = g.record_ids[eid]
    if len(recs) < 2:
        return []
    mid = (len(recs) + 1) // 2
    return [
        [
            dataclasses.replace(
                g, id=f"{g.id}.{k + 1}", record_ids={eid: part}, split_from_larger=True
            )
        ]
        for k, part in enumerate((recs[:mid], recs[mid:]))
    ]


def entity_in(graph: SpecGraph, g: FillGroup, eid: str) -> HeroEntityIn:
    e = graph.entities[eid]
    by_key = {f.key: f for f in e.fields}
    rules = []
    for row in g.write_fields[eid]:
        f = by_key.get(row["key"])
        rules.append(
            FieldRuleIn(
                key=row["key"],
                type=row["type"],
                rule=row["rule"],
                allowed=list(f.enum_values) if f else [],
            )
        )
    recs = []
    for rid in g.record_ids[eid]:
        h = graph.hero[rid]
        recs.append(
            RecIn(
                id=rid,
                state=h.state,
                set_by_code=[f"{k}={v}" for k, v in sorted(h.content.items())],
            )
        )
    return HeroEntityIn(
        id=eid,
        name=e.name,
        klass=e.klass,
        statuses=list(e.lifecycle.states),
        fields=rules,
        derived=[f"{d['field']} from {d['from_entity']}.{d['from_field']}" for d in g.derived[eid]],
        media=list(g.image_brief_for[eid]),
        records=recs,
    )


def filled_map(out: HeroOut, record_ids: Any = ()) -> dict[str, dict[str, Any]]:
    """What apply_fill merges. Every record of the call is listed, also one with no writable field (code set all of it)."""
    m: dict[str, dict[str, Any]] = {rid: {"content": {}} for rid in record_ids}
    for v in sorted(out.values, key=lambda x: (x.record, x.field)):
        m.setdefault(v.record, {"content": {}})["content"][v.field] = v.value
    for b in out.briefs:
        m.setdefault(b.record, {"content": {}})["image_brief"] = brief_text(b)
    return m


def allowed_names_of(graph: SpecGraph, spec_input: Any) -> list[str]:
    names = {a.name for a in graph.apps.values()}
    brand = getattr(getattr(spec_input, "brand", None), "values", ()) or ()
    names |= {str(b) for b in brand if b}
    return sorted(names)


async def hero_step(ctx: RunContext) -> None:
    cfg = ctx.config
    base_versions = call_versions(ctx, CALL_ID, VERSION, prompt_version(), out_schema_version())
    if not ctx.graph.hero:

        async def mint() -> Patch:
            rnd = random.Random(f"{ctx.program_id}:{ctx.input_hash}:hero")  # noqa: S311
            sk = mint_hero_skeleton(ctx.graph, rnd, config=cfg)
            return {"hero": {k: v.model_dump(mode="json") for k, v in sorted(sk.items())}}

        skel_versions = dataclasses.replace(base_versions, call_spec="hero.skeleton@1")
        ctx.apply(await ctx.batch("hero", "skeleton", skel_versions, mint))
    snap = ctx.graph.model_copy(deep=True)
    groups = fill_plan(snap, config=cfg)
    if not groups:
        return
    lang = snap.meta.source_language
    pool = pool_of(lang)
    names = [
        "people: " + "; ".join(pool.pick_people(8, random.Random(f"pool:{lang}"))),  # noqa: S311
        "places: " + "; ".join(pool.pick_places(6, random.Random(f"place:{lang}"))),  # noqa: S311
    ]
    allowed = allowed_names_of(snap, ctx.spec_input)
    facts = facts_text(snap, ctx.spec_input)
    gloss = glossary_block(snap, config=cfg)
    ents_all = snap.entities

    def make_input(items: list[FillGroup]) -> HeroIn:
        ents = [entity_in(snap, g, e) for g in items for e in g.entities]
        ctx_lines = sorted(
            {
                f"{c} {ents_all[c].name} ({ents_all[c].klass})"
                for g in items
                for c in g.context_entities
                if c in ents_all
            }
        )
        return HeroIn(
            language=lang,
            country=snap.meta.country,
            profile=snap.meta.profile,
            facts=facts,
            glossary=gloss,
            names=names,
            allowed_names=allowed,
            entities=ents,
            context=ctx_lines,
        )

    from mde_spec_agent.lanes.screens.mock_hero.validate import validate as local_validate

    def scratch_validator(inp: HeroIn, out: HeroOut) -> list[str]:
        scratch = snap.model_copy(deep=True)
        ids = [r.id for e in inp.entities for r in e.records]
        res = apply_fill(scratch, filled_map(out, ids), allowed_names=inp.allowed_names)
        return [f"{rid}: {p}" for rid, probs in sorted(res.rejected.items()) for p in probs]

    cs = dataclasses.replace(SPEC, validators=(local_validate, scratch_validator))

    def producer(group: FillGroup):
        async def produce() -> Patch:
            if (
                ctx.runner is None
            ):  # code-only: values come from the locale pools, nothing is degraded
                results = [
                    (
                        [group],
                        RunResult(output=cs.fallback(make_input([group])), used_fallback=True),
                    )
                ]
            else:
                results = await run_split(
                    ctx, cs, [group], make_input, lambda g: g.id, "hero", split=split_group
                )
            hero: dict[str, Any] = {}
            flags: list[dict[str, Any]] = []
            for items, res in results:
                scratch = snap.model_copy(deep=True)
                touched = {rid for g in items for ids in g.record_ids.values() for rid in ids}
                done = apply_fill(
                    scratch, filled_map(res.output, sorted(touched)), allowed_names=allowed
                )
                for rid in sorted(touched):
                    hero[rid] = scratch.hero[rid].model_dump(mode="json")
                for g in items:
                    if res.flagged:
                        flags.append(degraded_flag("hero", g.id, res.reason or "fallback"))
                for rid, probs in sorted(done.rejected.items()):
                    flags.append(degraded_flag("hero", rid, "; ".join(probs)))
            return {"hero": hero, "flags": flags}

        return produce

    coros = [ctx.batch("hero", g.id, base_versions, producer(g)) for g in groups]
    patches = await gather_batches(coros)
    for p in patches:
        ctx.apply(p)
        ctx.flags.extend(p.get("flags", []))
    derived = derive_shared_values(ctx.graph)
    ctx.emit(
        "notice",
        what="hero.done",
        step="hero",
        records=len(ctx.graph.hero),
        filled=sum(1 for h in ctx.graph.hero.values() if h.filled),
        derived=len(derived),
    )
