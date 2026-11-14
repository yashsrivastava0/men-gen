"""The `screens` step: cards, source copy and COULD entries for every release screen (doc 18 1.2 step 10).

Batches of screens = clamp(floor(0.5 x writer max output / average card tokens), 1, cards_per_call_max). Every batch is a
checkpointed unit (`ctx.batch`); its patch is applied in SORTED batch order after all batches finished, so a resumed run
ends with the same bytes. Gate 1 runs inside each call (the runner's one re-ask per node). A flagged call marks its
screens degraded and appends to `ctx.flags`; a budget refusal or a stop request propagates untouched.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from mde_spec_agent.lanes.screens.batching import gather_batches, run_split
from mde_spec_agent.lanes.screens.common import (
    Gate1,
    call_versions,
    chunked,
    degraded_flag,
    facts_text,
    glossary_block,
    lane_rules,
    log_rcode,
)
from mde_spec_agent.lanes.screens.scrn.build import assemble, screens_patch
from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn, SlotIn
from mde_spec_agent.lanes.screens.scrn.slots import build_slot, incoming_edges, vocab_lines
from mde_spec_agent.lanes.screens.scrn.spec import (
    CALL_ID,
    SPEC,
    VERSION,
    out_schema_version,
    prompt_version,
)
from mde_spec_agent.lanes.screens.scrn.validate import graph_validators
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch, merge_patches
from mde_spec_core.callkit.fusion import items_per_call
from mde_spec_core.callkit.prompt import estimate_tokens
from mde_spec_core.graph import SpecGraph
from mde_spec_core.render import render_card


def release_targets(graph: SpecGraph) -> list[str]:
    """Screens that get a model-written card: the release, except the system set (library cards, policy-locked)."""
    rows = [s for s in graph.screens.values() if s.status == "in" and not s.system]
    rows.sort(key=lambda s: (s.app_id, s.module_id or "", s.id))
    return [s.id for s in rows]


def avg_card_tokens(graph: SpecGraph, ids: list[str], writer: Any) -> float:
    if not ids:
        return 1.0
    est = [estimate_tokens(render_card(graph.screens[i]), writer) for i in ids]
    return (
        sum(est) / len(est) * 1.4 + 120
    )  # the model's card is richer than the baseline, plus its copy


def _criticality(c: dict[str, Any], graph: SpecGraph) -> tuple[int, int]:
    import re

    if c["basis_kind"] == "journey_moment":
        m = re.search(r"(FLW-\w+) step (\d+)", c["basis"])
        f = graph.flows.get(m.group(1)) if m else None
        if f and f.primary and m and int(m.group(2)) in f.moments_that_matter:
            return (0, 0)  # a signature moment of a primary journey
        return (1, 0 if f and f.primary else 1)
    return (2, 0)


def trim_could(patches: list[Patch], graph: SpecGraph, cap_by_profile: dict[str, int]) -> int:
    """Keep each app within its COULD cap, signature moments first (V-42's own advice: trim by criticality). A tier C entry
    (the fixed stack cannot build it) is NOT dropped silently: it stays an optional entry with its tier, goes first when
    the cap bites, and each one that survives is listed as a ``capability_risk`` flag for G2 (BUG-DG-2). Works on the patch
    dicts so the checkpointed patches stay as written."""
    cap = cap_by_profile[graph.meta.profile]
    per_app: dict[str, list[tuple[tuple[bool, int, int], str, int]]] = defaultdict(list)
    for p in patches:
        for sid, node in sorted(p["tables"].get("screens", {}).items()):
            for i, c in enumerate(node["could"]):
                per_app[node["app_id"]].append(
                    ((c["tier"] == "C", *_criticality(c, graph)), sid, i)
                )
    drop: set[tuple[str, int]] = set()
    for rows in per_app.values():
        rows.sort()
        drop |= {(sid, i) for _, sid, i in rows[cap:]}
    for p in patches:
        for sid, node in sorted(p["tables"].get("screens", {}).items()):
            node["could"] = [c for i, c in enumerate(node["could"]) if (sid, i) not in drop]
            for c in node["could"]:
                if c["tier"] == "C":
                    p.setdefault("flags", []).append(_tier_c_flag(sid, c))
    return len(drop)


def _tier_c_flag(screen_id: str, could: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "capability_risk",
        "capability": f"could:{could['id']}",
        "label": "an optional idea that needs a capability the fixed stack cannot build",
        "tier": "C",
        "production_recommended": False,
        "implied_by": [f"COULD {screen_id}.{could['id']}"],
        "note": "kept as an optional entry with its tier; it is outside the approved hash and is built only if the stack allows it",
    }


async def screens_step(ctx: RunContext) -> None:
    if ctx.runner is None:  # the zero-cost preview: the baseline cards stand (no model call)
        return
    cfg, rules = ctx.config, lane_rules()
    snap = ctx.graph.model_copy(deep=True)  # the frozen graph: batches read it, never the live one
    lang = snap.meta.source_language
    ids = release_targets(snap)
    if not ids:
        return
    copy0 = dict(snap.locale_copy.get(lang, {}))
    incoming = incoming_edges(snap)
    slots = {
        i: build_slot(
            snap.screens[i], snap, cfg, rules, research=ctx.research, copy=copy0, incoming=incoming
        )
        for i in ids
    }
    per_call = items_per_call(
        ctx.writer, avg_card_tokens(snap, ids, ctx.writer), cfg.fusion.cards_per_call_max, cfg
    )
    batches = chunked(ids, per_call)
    facts = facts_text(snap, ctx.spec_input)
    gloss = glossary_block(snap, config=cfg)
    vocab = vocab_lines()
    gate1 = Gate1(snap, cfg, ctx.research)
    cs = dataclasses.replace(
        SPEC,
        validators=(*SPEC.validators, *graph_validators(snap, cfg, rules, gate1)),
    )
    versions = call_versions(ctx, CALL_ID, VERSION, prompt_version(), out_schema_version())

    def make_input(items: list[SlotIn]) -> ScrnIn:
        return ScrnIn(
            language=lang,
            profile=snap.meta.profile,
            vocab=vocab,
            facts=facts,
            glossary=gloss,
            slots=items,
        )

    def producer(n: int, batch_ids: list[str]):
        async def produce() -> Patch:
            results = await run_split(
                ctx, cs, [slots[i] for i in batch_ids], make_input, lambda s: s.id, "screens"
            )
            patches: list[Patch] = []
            flags: list[dict[str, Any]] = []
            rcode: list[dict[str, str]] = []
            for items, res in results:
                inp = make_input(items)
                built = assemble(snap, inp, res.output, cfg, rules, ids=[s.id for s in items])
                flags += built.flags
                rcode += built.notes.rows
                if res.flagged:
                    for sid, s in sorted(built.screens.items()):
                        s.degraded = True
                        flags.append(degraded_flag("screens", sid, res.reason or "fallback"))
                patches.append(screens_patch(built, lang))
            merged = merge_patches(patches)
            merged["flags"] = flags
            merged["rcode"] = rcode
            return merged

        return produce

    coros = [
        ctx.batch("screens", f"scrn-{n:03d}-{b[0]}", versions, producer(n, b))
        for n, b in enumerate(batches)
    ]
    patches = await gather_batches(coros)  # results come back in batch order
    trimmed = trim_could(patches, snap, cfg.profiles.could_cap_per_app)
    for p in patches:
        ctx.apply(p)
        ctx.flags.extend(p.get("flags", []))
        log_rcode(ctx, p.get("rcode", []), step="screens")
    degraded = sum(1 for i in ids if ctx.graph.screens[i].degraded)
    ctx.emit(
        "notice",
        what="screens.done",
        step="screens",
        cards=len(ids),
        degraded=degraded,
        could_trimmed=trimmed,
    )
