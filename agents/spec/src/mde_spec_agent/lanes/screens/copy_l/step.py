"""The `copyl` step (doc 18 1.2 step 13): locale passes behind `screens`. For each non-source language the strings that
have no text yet are translated in batches of at most `fusion.strings_per_locale_call` (a string that already has a
locale text is never re-translated: the step is idempotent). Every non-source locale is flagged `needs_native_review` with
the key `review.<locale>` in its own copy file; a locale written by the fallback is also marked degraded."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.lanes.screens.batching import gather_batches, run_split
from mde_spec_agent.lanes.screens.common import (
    call_versions,
    chunked,
    degraded_flag,
    facts_text,
    glossary_block,
)
from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, StrIn
from mde_spec_agent.lanes.screens.copy_l.spec import (
    CALL_ID,
    SPEC,
    VERSION,
    out_schema_version,
    prompt_version,
)
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.callkit.fusion import items_per_call
from mde_spec_core.callkit.prompt import estimate_tokens

REVIEW = "needs_native_review"


def review_key(locale: str) -> str:
    return f"review.{locale}"


async def copyl_step(ctx: RunContext) -> None:
    if ctx.runner is None:  # the zero-cost preview: other languages are not written (marked at G2)
        return
    cfg = ctx.config
    snap = ctx.graph.model_copy(deep=True)
    src = snap.meta.source_language
    targets = [loc for loc in snap.meta.languages if loc != src]
    source = snap.locale_copy.get(src, {})
    if not targets or not source:
        return
    facts = facts_text(snap, ctx.spec_input)
    gloss = glossary_block(snap, config=cfg)
    versions = call_versions(ctx, CALL_ID, VERSION, prompt_version(), out_schema_version())
    coros: list[Any] = []
    for loc in targets:
        have = snap.locale_copy.get(loc, {})
        todo = sorted(k for k in source if k not in have)
        if not todo:
            continue
        avg = sum(estimate_tokens(k + source[k], ctx.writer) for k in todo) / len(todo) * 1.5 + 10
        per = items_per_call(ctx.writer, avg, cfg.fusion.strings_per_locale_call, cfg)
        for n, keys in enumerate(chunked(todo, per)):
            coros.append(
                ctx.batch(
                    "copyl",
                    f"{loc}:{n:03d}",
                    versions,
                    _producer(ctx, SPEC, loc, src, keys, source, facts, gloss),
                )
            )
    if not coros:
        return
    patches = await gather_batches(coros)
    for p in patches:
        ctx.apply(p)
        ctx.flags.extend(p.get("flags", []))
    for loc in targets:
        if any(f["node"].startswith(f"{loc}:") for p in patches for f in p.get("flags", [])):
            ctx.graph.locale_copy.setdefault(loc, {})[review_key(loc)] = REVIEW + "; degraded"
    ctx.emit("notice", what="copyl.done", step="copyl", locales=targets, batches=len(patches))


def _producer(ctx, cs, loc, src, keys, source, facts, gloss):
    def make_input(items: list[str]) -> CopyLIn:
        return CopyLIn(
            language=loc,
            source_language=src,
            glossary=gloss,
            facts=facts,
            strings=[StrIn(key=k, text=source[k]) for k in items],
        )

    async def produce() -> Patch:
        results = await run_split(ctx, cs, keys, make_input, lambda k: k, "copyl")
        texts: dict[str, str] = {}
        flags: list[dict[str, Any]] = []
        degraded = False
        for items, res in results:
            for s in res.output.strings:
                texts[s.key] = s.text
            if res.flagged:
                degraded = True
                flags.append(degraded_flag("copyl", f"{loc}:{items[0]}", res.reason or "fallback"))
        texts[review_key(loc)] = REVIEW + ("; degraded" if degraded else "")
        return {"locale_copy": {loc: dict(sorted(texts.items()))}, "flags": flags}

    return produce
