"""The `gloss` step: right after the Freeze, before Screens, the glossary goes into the source language and every extra
language in ONE call (`spec.gloss_l`, route fast).

The glossary is code-built at the Freeze (`glossary.build_glossary`, at most 0.7k tokens) and flattened into
`graph.glossary` as key -> text. This step puts every text through one call; the result goes into the patch's `glossary`
(key -> source-language text) and, per extra language, into `locale_copy[locale]` under the reserved key prefix
`glossary.`. A single-language app makes no call and costs nothing. If the call fails after its re-ask the Brief's own
terms stay, the extra locales are marked degraded and the locale calls run flagged."""

from __future__ import annotations

import json
from typing import Any

from mde_spec_agent.calls.base import Truncated
from mde_spec_agent.lanes.design._versions import versions_of
from mde_spec_agent.lanes.design.gloss_l.schema import GlossEntry, GlossIn, GlossItem
from mde_spec_agent.lanes.design.gloss_l.spec import SPEC
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core import glossary as glossary_mod
from mde_spec_core.canon import canonical_json
from mde_spec_core.graph import GraphMeta, SpecGraph

STEP = "gloss"
KEY_PREFIX = "glossary."  # reserved prefix of glossary entries in `locale_copy[locale]`
DEGRADED_FACT = "glossary_degraded"
MAX_SPLIT_DEPTH = 3


def flat_glossary(graph: SpecGraph) -> dict[str, str]:
    """The Freeze's flat glossary (key -> text); built the same way when the graph is not frozen yet."""
    if graph.meta.frozen and graph.glossary:
        return dict(sorted(graph.glossary.items()))
    dirs = sorted(graph.directions.values(), key=lambda d: d.id)
    gl = glossary_mod.build_glossary(graph, dirs[0] if dirs else None)
    flat: dict[str, str] = {}
    for group in ("entities", "roles", "terms"):
        for k, v in sorted(gl.get(group, {}).items()):
            flat[f"{group}.{k}"] = v if isinstance(v, str) else canonical_json(v)
    for verb in gl.get("verbs", []):
        flat[f"verbs.{verb}"] = verb
    if gl.get("voice"):
        flat["voice"] = str(gl["voice"])
    return flat


def _entity_parts(value: str) -> dict[str, Any] | None:
    try:
        data = json.loads(value)
    except ValueError:
        return None
    ok = isinstance(data, dict) and isinstance(data.get("term"), str)
    return data if ok and isinstance(data.get("statuses", []), list) else None


def leaf_texts(flat: dict[str, str]) -> list[str]:
    """Every distinct text that needs a translation, in a stable order (an entity's JSON gives its term and statuses)."""
    seen: dict[str, None] = {}
    for key in sorted(flat):
        value = flat[key]
        ent = _entity_parts(value) if key.startswith("entities.") else None
        texts = [ent["term"], *[str(s) for s in ent.get("statuses", [])]] if ent else [value]
        for t in texts:
            if t.strip():
                seen.setdefault(t, None)
    return list(seen)


def rendered(flat: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    """The flat glossary with every leaf text replaced through `mapping` (a text with no entry stays as it is)."""
    out: dict[str, str] = {}
    for key, value in flat.items():
        ent = _entity_parts(value) if key.startswith("entities.") else None
        if ent:
            ent = {
                **ent,
                "term": mapping.get(ent["term"], ent["term"]),
                "statuses": [mapping.get(str(s), str(s)) for s in ent.get("statuses", [])],
            }
            out[key] = canonical_json(ent)
        else:
            out[key] = mapping.get(value, value)
    return out


async def _translate(
    ctx: RunContext, inp: GlossIn, tag: str, depth: int = 0
) -> list[GlossEntry] | None:
    """One call; on truncation the items split in halves (each half is its own node for the re-ask rule)."""
    assert ctx.runner is not None
    try:
        res = await ctx.runner.run(SPEC, inp, node_ids=(tag,), step=STEP)
    except Truncated:
        if len(inp.items) < 2 or depth >= MAX_SPLIT_DEPTH:
            return None
        mid = len(inp.items) // 2
        parts = []
        for n, items in enumerate((inp.items[:mid], inp.items[mid:])):
            got = await _translate(
                ctx, inp.model_copy(update={"items": items}), f"{tag}.{n}", depth + 1
            )
            if got is None:
                return None
            parts += got
        return parts
    return None if res.used_fallback else list(res.output.entries)


def _degraded_meta(meta: GraphMeta) -> dict[str, Any]:
    """Mark the graph: a `degraded_locales` field when the graph has one, else the fact `glossary_degraded`."""
    if "degraded_locales" in GraphMeta.model_fields:
        return {
            "degraded_locales": sorted(
                set(getattr(meta, "degraded_locales", [])) | set(_extras(meta))
            )
        }
    return {"facts": sorted({*meta.facts, DEGRADED_FACT})}


def _extras(meta: GraphMeta) -> list[str]:
    return [lang for lang in meta.languages if lang != meta.source_language]


async def gloss(ctx: RunContext) -> None:
    g = ctx.graph
    flat = flat_glossary(g)
    ctx.plan["glossary"] = flat
    extras = _extras(g.meta)
    texts = leaf_texts(flat)
    if not g.meta.frozen and flat:  # no Freeze stored it: keep the code-built glossary
        ctx.apply({"glossary": flat})
    if not extras or not texts:
        return
    items = [GlossItem(id=f"g{n + 1:03d}", text=t) for n, t in enumerate(texts)]
    inp = GlossIn(source_language=g.meta.source_language, languages=extras, items=items)

    async def produce() -> Patch:
        entries = await _translate(ctx, inp, "glossary") if ctx.runner is not None else None
        if (
            entries is None
        ):  # the Brief's own terms are used and the locale calls are marked degraded
            return {
                "meta": _degraded_meta(g.meta),
                "flags": [
                    {
                        "kind": "degraded_locales",
                        "locales": list(extras),
                        "reason": "glossary translation failed after its re-ask; the Brief's own terms are used",
                    }
                ],
            }
        by_text = {i.id: i.text for i in items}
        maps: dict[str, dict[str, str]] = {}
        for e in entries:
            maps.setdefault(e.language, {})[by_text[e.id]] = e.text.strip()
        src = rendered(flat, maps.get(g.meta.source_language, {}))
        return {
            "glossary": src,
            "locale_copy": {
                lang: {f"{KEY_PREFIX}{k}": v for k, v in rendered(flat, maps.get(lang, {})).items()}
                for lang in extras
            },
            "flags": [],
        }

    patch = await ctx.batch(STEP, "glossary", versions_of(ctx.versions, SPEC), produce)
    ctx.apply(patch)
    ctx.flags.extend(patch.get("flags", []))
