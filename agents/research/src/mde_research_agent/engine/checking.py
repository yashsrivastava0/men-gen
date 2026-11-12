"""The checker, inside the topic (RC-2): every verified item is read by a second model before the coverage audit.

``check_topic`` runs after a topic's pages were extracted and BEFORE ``update_coverage``, so a ``not_supported`` item
reopens its slot and the gap analyst sees the truth. Items go in batches of ``CHECK_BATCH``; identical
(claim, quote, data) items are checked once across the whole run (in flight or done); batches of different topics run
in parallel under the model semaphore. A failed call or a missing verdict leaves the item ``unchecked`` (flagged, never
trusted, never dropped silently). After the hard time limit, a budget stop, a Skip or a topic cancel no new batch
starts: what is left is ``unchecked``.

Pure helpers (``passage_window``, ``null_paths``, ``meets_minimum``) are separate so they can be tested alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from mde_core.errors import BudgetExceeded, CallCancelled, CassetteMissing, LiveCallForbidden
from mde_research_agent.calls import check as check_call
from mde_research_agent.catalog.models import LanesConfig
from mde_research_agent.core.numbers import claim_figures, figures, missing_figures
from mde_research_agent.core.quotes import verify_quote
from mde_research_agent.domain.atoms import NON_WIRE, is_identifying
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.state import AtomRecord, PageRecord, TargetState

CHECK_BATCH = 15
PASSAGE_WORDS = 150
MAX_DATA_CHARS = 2500
_PATH_HEAD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")
_LOOSE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?(?:\..+)$")
_PATH = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?(?:\.([A-Za-z_][A-Za-z0-9_]*))?$")
_STOP = (BudgetExceeded, CassetteMissing, LiveCallForbidden)


# --- pure helpers ---------------------------------------------------------------------------------------------


def passage_window(page: PageRecord, quote: str, words: int = PASSAGE_WORDS) -> str:
    """About ``words`` words of the stored page around ``quote`` (the quote itself in the middle); the quote alone
    when it cannot be located."""
    res = verify_quote(page.norm, quote)
    if not res.ok:
        return " ".join(quote.split())
    side = max(10, (words - len(quote.split())) // 2)
    left = page.text[max(0, res.start - 1500) : res.start].split()[-side:]
    mid = " ".join(page.text[res.start : res.end].split())
    right = page.text[res.end : res.end + 1500].split()[:side]
    return " ".join([*left, mid, *right])


def null_paths(
    data: dict[str, Any],
    paths: list[str],
    identifying: Callable[[str, str], bool] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """``data`` without the fields the checker said the page does not state. A path is ``field``, ``field[i]`` or
    ``field[i].sub``; a top-level scalar becomes null, a list becomes empty, a list element is removed, a nested
    attribute becomes null. Unknown paths are ignored. Returns ``(new data, the paths applied)``."""
    out: dict[str, Any] = json.loads(json.dumps(data))
    applied: list[str] = []
    removals: list[tuple[str, int]] = []
    for p in paths:
        m = _PATH.match(p.strip())
        if m:
            name, idx, sub = m[1], m[2], m[3]
        else:  # "pricing_model.free trial": the part after the dot is prose, not a field: null the whole field
            loose = _LOOSE.match(p.strip())
            if not loose:
                continue
            name, idx, sub = loose[1], loose[2], None
        if name not in out:
            continue
        value = out[name]
        if idx is None:
            if sub is not None:
                continue
            out[name] = [] if isinstance(value, list) else None
            applied.append(p)
        elif isinstance(value, list) and int(idx) < len(value):
            if sub is None:
                removals.append((name, int(idx)))
                applied.append(p)
            elif isinstance(value[int(idx)], dict) and sub in value[int(idx)]:
                if identifying is not None and identifying(name, sub):
                    removals.append(
                        (name, int(idx))
                    )  # a required attribute cannot be null: the element goes
                else:
                    value[int(idx)][sub] = None
                applied.append(p)
    for name, i in sorted(
        set(removals), key=lambda t: (t[0], -t[1])
    ):  # remove from the end so indexes stay valid
        if isinstance(out.get(name), list) and i < len(out[name]):
            del out[name][i]
    return out, applied


def _present(value: Any) -> bool:
    if isinstance(value, list):
        return any(_present(v) for v in value)
    if isinstance(value, dict):
        return any(_present(v) for v in value.values())
    return value not in (None, "", False) or value is False


def meets_minimum(kind: str, data: dict[str, Any], lanes: LanesConfig) -> bool:
    """Does the item still have what its kind needs after fields were nulled?"""
    spec = lanes.kinds[kind]
    if any(not _present(data.get(f)) for f in spec.min_all):
        return False
    return not spec.min_any or any(_present(data.get(f)) for f in spec.min_any)


def pair_key(a: AtomRecord) -> str:
    """Identical (claim, quote, data) items are checked once."""
    raw = json.dumps([a.claim, a.quote, a.data], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


# --- the check ------------------------------------------------------------------------------------------------


def _stopped(ctx: RunContext, ts: TargetState) -> bool:
    return (
        ctx.run_cancel.cancelled
        or ts.cancel.cancelled
        or ctx.budget_stopped
        or ctx.budget.time_up_hard()
        or ctx.budget.over_money()
    )


async def _announce(ctx: RunContext, ts: TargetState, a: AtomRecord, level: str) -> None:
    page = ctx.pages_by_id[a.page_id]
    await ctx.events.emit(
        "fact.verified",
        target_id=a.target_id,
        phase="verify",
        params={"domain": page.domain},
        detail={
            "kind": a.kind,
            "label": a.label,
            "domain": page.domain,
            "level": level,
            "topic": ts.target.label,
            "fact_id": a.id,
        },
    )


async def _mark_unchecked(ctx: RunContext, ts: TargetState, atoms: list[AtomRecord]) -> None:
    for a in atoms:
        a.flags.add("unchecked")
        await _announce(ctx, ts, a, "unchecked")


async def _apply(
    ctx: RunContext, ts: TargetState, a: AtomRecord, v: check_call.CheckVerdict | None
) -> None:
    if v is None:
        await _mark_unchecked(ctx, ts, [a])
        return
    a.checked = True
    a.verdict = v.verdict
    if v.verdict == "not_supported":
        a.status = "rejected"
        a.reject_reason = "not_supported"
        ctx.drops.not_supported += 1
        await ctx.events.emit(
            "fact.rejected",
            target_id=a.target_id,
            phase="verify",
            detail={
                "reason": "not_supported",
                "topic": ts.target.label,
                "label": a.label,
                "fact_id": a.id,
            },
        )
        return
    # the checker may name a field that CODE set (legal_adjacent, url, the *_variants): those are not the model's claims
    owned = NON_WIRE.get(a.kind, frozenset())
    paths = [
        p for p in v.unsupported_fields if (h := _PATH_HEAD.match(p)) is None or h[1] not in owned
    ]
    if paths:
        data, applied = null_paths(
            a.data, paths, identifying=lambda field, sub: is_identifying(a.kind, field, sub)
        )
        if applied:
            a.data = data
            a.unsupported_fields = applied
            if not meets_minimum(a.kind, data, ctx.catalog.lanes):
                a.status = "rejected"
                a.reject_reason = "unsupported_field"
                ctx.drops.unsupported_field += 1
                await ctx.events.emit(
                    "fact.rejected",
                    target_id=a.target_id,
                    phase="verify",
                    detail={
                        "reason": "unsupported_field",
                        "topic": ts.target.label,
                        "label": a.label,
                    },
                )
                return
    if v.verdict == "partial":
        a.flags.add("partial_support")
        a.unsupported_sentences = list(v.unsupported_sentences)
    await _announce(ctx, ts, a, v.verdict)


def tightened_claim(claim: str, numbers: list[int]) -> tuple[str, list[str]] | None:
    """The claim without the sentences the checker named, and what was dropped; None when nothing can be dropped
    (no valid number, or every sentence was named, which would leave no claim). Only removes words, never adds."""
    sentences = check_call.claim_sentences(claim)
    drop = sorted({n for n in numbers if 1 <= n <= len(sentences)})
    if not drop or len(drop) >= len(sentences):
        return None
    kept = [s for n, s in enumerate(sentences, 1) if n not in drop]
    return " ".join(kept), [sentences[n - 1] for n in drop]


async def tighten_and_recheck(
    atoms: list[AtomRecord],
    ask: Callable[[list[AtomRecord]], Awaitable[dict[str, check_call.CheckVerdict | None]]],
) -> list[AtomRecord]:
    """Partial items whose checker named unsupported sentences: drop those sentences and ask the same checker once more.

    ``ask`` puts a list of items to the checker and returns ``{pair_key: verdict or None}``; the engine and the experiment
    harness each bring their own. The item climbs to ``supported`` only if the checker then says so with nothing left to
    null, and every figure of the shorter claim is still in the quote (it cannot be otherwise: words were only removed).
    Otherwise the original claim is restored and the item stays partial. A bare second question about an unchanged
    claim is never asked: it would only resample a noisy answer (7 of 20 partials came back supported on a plain re-ask,
    D-159). Returns the items that were tried; those that climbed carry the flag ``tightened``.
    """
    tried: list[AtomRecord] = []
    for a in atoms:
        if a.status != "verified" or a.verdict != "partial" or not a.unsupported_sentences:
            continue
        cut = tightened_claim(a.claim, a.unsupported_sentences)
        if cut is None:
            continue
        a.claim_original, (a.claim, a.dropped_sentences) = a.claim, cut
        tried.append(a)
    if not tried:
        return tried
    unique: dict[str, AtomRecord] = {}
    for a in tried:
        unique.setdefault(pair_key(a), a)
    results = await ask(list(unique.values()))
    for a in tried:
        v = results.get(pair_key(a))
        ok = (
            v is not None
            and v.verdict == "supported"
            and not v.unsupported_fields
            and not missing_figures(claim_figures(a.claim, a.label, a.data), figures(a.quote))
        )
        if not ok:
            _restore(a)
            continue
        a.verdict = "supported"
        a.flags.discard("partial_support")
        a.flags.add("tightened")
    return tried


async def _tighten(ctx: RunContext, ts: TargetState, atoms: list[AtomRecord]) -> None:
    async def ask(items: list[AtomRecord]) -> dict[str, check_call.CheckVerdict | None]:
        keyed = [(pair_key(a), a) for a in items]
        if _stopped(ctx, ts):
            return {}
        out: dict[str, check_call.CheckVerdict | None] = {}
        batches = [keyed[i : i + CHECK_BATCH] for i in range(0, len(keyed), CHECK_BATCH)]
        for got in await asyncio.gather(*[_run_batch(ctx, ts, b) for b in batches]):
            out.update(got)
        return out

    for a in await tighten_and_recheck(atoms, ask):
        ctx.count("tightened")
        if "tightened" in a.flags:
            ctx.count("tightened_supported")
            await _announce(ctx, ts, a, "supported")


def _restore(a: AtomRecord) -> None:
    if a.claim_original is not None:
        a.claim, a.claim_original, a.dropped_sentences = a.claim_original, None, []


async def _run_batch(
    ctx: RunContext, ts: TargetState, items: list[tuple[str, AtomRecord]]
) -> dict[str, check_call.CheckVerdict | None]:
    """One model call for a batch of unique items; ``{pair key: verdict or None}``. Never raises for model trouble."""
    out: dict[str, check_call.CheckVerdict | None] = {k: None for k, _ in items}
    if _stopped(ctx, ts):
        return out
    wire: list[check_call.CheckItem] = []
    ids: dict[str, str] = {}
    for n, (key, a) in enumerate(items):
        page = ctx.pages_by_id[a.page_id]
        cid = f"C{n + 1}"
        ids[cid] = key
        wire.append(
            check_call.CheckItem(
                id=cid,
                claim=a.claim,
                quote=a.quote,
                data=json.dumps(a.data, ensure_ascii=False, separators=(",", ":"))[:MAX_DATA_CHARS],
                passage=passage_window(page, a.quote),
                page_title=page.title,
                page_domain=page.domain,
                about_region="" if a.about_region == "unknown" else a.about_region,
                about_entity=a.about_entity or "",
            )
        )
    try:
        async with ctx.llm_sem:
            with ctx.stage("check"):
                res = await ctx.runner.run(check_call.SPEC, check_call.CheckIn(items=wire))
    except _STOP as exc:
        ctx.budget_stopped = ctx.budget_stopped or isinstance(exc, BudgetExceeded)
        return out
    except CallCancelled:
        return out
    ctx.budget.add_llm_cost(res.cost_micros, "check")
    if res.failed:
        ctx.failures.record("llm", res.reason or "failed", ts.target.id)
        await ctx.warn("model_failed", target_id=ts.target.id)
        return out
    for v in res.output.verdicts:
        key = ids.get(v.id)
        if key is not None:
            out[key] = v
    return out


async def check_topic(ctx: RunContext, ts: TargetState) -> None:
    """Check every verified, not yet checked item of the topic. Must run before the coverage audit."""
    atoms = [
        a
        for a in ts.atoms
        if a.status == "verified" and not a.checked and "unchecked" not in a.flags
    ]
    if not atoms:
        return
    atoms.sort(key=lambda a: (a.kind, a.norm_key, a.page_id, a.quote))
    if _stopped(ctx, ts):
        await _mark_unchecked(ctx, ts, atoms)
        return
    mine: dict[str, asyncio.Future[check_call.CheckVerdict | None]] = {}
    waiting: dict[str, asyncio.Future[check_call.CheckVerdict | None]] = {}
    to_run: list[tuple[str, AtomRecord]] = []
    keyed = [(pair_key(a), a) for a in atoms]
    for key, a in keyed:
        if key in mine or key in waiting:
            continue
        fut = ctx.check_inflight.get(key)
        if fut is not None:
            waiting[key] = fut  # another topic is checking the same item: wait for its answer
            continue
        fut = asyncio.get_running_loop().create_future()
        ctx.check_inflight[key] = fut
        mine[key] = fut
        to_run.append((key, a))
    results: dict[str, check_call.CheckVerdict | None] = {}
    try:
        batches = [to_run[i : i + CHECK_BATCH] for i in range(0, len(to_run), CHECK_BATCH)]
        for got in await asyncio.gather(*[_run_batch(ctx, ts, b) for b in batches]):
            results.update(got)
    finally:
        for key, fut in mine.items():
            if not fut.done():
                fut.set_result(results.get(key))
    for key, fut in waiting.items():
        results[key] = await fut
    for key, a in keyed:
        await _apply(ctx, ts, a, results.get(key))
    await _tighten(ctx, ts, [a for _, a in keyed])
