"""Assemble the committed ``research.json`` (``research.v5``) and the internal ``research_evidence`` from a finished run.

Code builds everything factual: items (grouped, sourced, flagged), norms (prevalence, recency), conflicts, the confirm
list, suggestions, defaults for builder needs, indexes by the Brief's ids, gaps, coverage, budget and the lane digests.
ONE model call (RSCH-3, no tools) only selects and orders the items per lane and writes a short ``use_note`` for each;
code drops any note that adds a number, name, date or URL the item does not contain, any id that was not offered, and
any note over 15 words (RV-05). If the call fails the deterministic ordering and the default notes are used. Sizes are
capped (RV-09): the lowest-ranked items go first and the drops are counted.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

import yaml

from mde_core.money import micros_to_usd
from mde_research_agent import SCHEMA_VERSION
from mde_research_agent.assemble import digest as dg
from mde_research_agent.assemble.items import BuiltItem, build_items
from mde_research_agent.assemble.notes import openers
from mde_research_agent.assemble.quality import (
    bn_coverage,
    TargetInfo,
    flagged_counts,
    lane_quality,
)
from mde_research_agent.assemble.quality import lane_items as lane_items_of
from mde_research_agent.assemble.store import assign_store, compact_item, tokens_of
from mde_research_agent.assemble.norms import (
    apply_norms,
    build_suggestions,
    compute_norms,
    detect_conflicts,
)
from mde_research_agent.calls import assemble as asm_call
from mde_research_agent.catalog.loader import DATA_DIR
from mde_research_agent.core.applies import BriefIndex
from mde_research_agent.core.hashing import content_hash
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.novelty import novel_tokens
from mde_research_agent.domain.plan import Dropped, Target
from mde_research_agent.domain.research import (
    Budget,
    Confirm,
    Coverage,
    DefaultProposed,
    Gap,
    GapReason,
    Profile,
    ResearchOutput,
    RunInfo,
    Slice,
)
from mde_research_agent.domain.research import (
    Dropped as DroppedCounts,
)
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.director import DirectorResult

MAX_NOTE_WORDS = 15
NOTE_TOKEN_ALLOWANCE = 24  # room per item for a use note the model writes (at most 15 words)
LEVEL_RANK = {"core": 0, "common": 1, "emerging": 2, "advanced": 3, "limited_evidence": 4}


def _defaults_data() -> dict[str, Any]:
    return yaml.safe_load((DATA_DIR / "defaults.yaml").read_text(encoding="utf-8")) or {}


def _lanes_of(b: BuiltItem, ctx: RunContext, targets: dict[str, Target]) -> list[str]:
    lanes = list(ctx.catalog.lanes.kinds[b.item.kind].lanes)
    t = targets.get(b.item.target_id)
    if t is not None:
        lanes += [t.lane, *t.also_lanes]
    return list(dict.fromkeys(lanes))


def _confirm(built: list[BuiltItem], reasons: dict[str, str]) -> list[Confirm]:
    out: list[Confirm] = []
    for b in built:
        it = b.item
        flags = set(it.flags)
        d = it.data
        if "conflict" in flags:
            why = reasons["conflict"]
        elif (it.kind in ("rule",) and it.impact == "high" and d.get("value")) or (
            it.kind == "service_option"
            and it.impact == "high"
            and (d.get("fees") or d.get("limits"))
        ):
            why = reasons["number"]
        elif "legal-adjacent" in flags and it.impact == "high":
            why = reasons["legal"]
        elif "single-source" in flags and it.impact == "high":
            why = reasons["single_source"]
        else:
            continue
        out.append(Confirm(item_id=it.id, why=why))
        if "needs-confirm" not in flags:
            b.item = it.model_copy(update={"flags": sorted(flags | {"needs-confirm"})})
    return out


def _defaults(
    built: list[BuiltItem], ctx: RunContext, text: dict[str, str]
) -> list[DefaultProposed]:
    by_need: dict[str, list[str]] = defaultdict(list)
    for b in built:
        by_need[ctx.catalog.lanes.kinds[b.item.kind].need].append(b.item.id)
    out = []
    for need in sorted(by_need):
        if need in text:
            ranked = sorted(by_need[need], key=lambda i: i)[:10]
            out.append(DefaultProposed(need=need, item_ids=ranked, default=text[need]))
    return out


def _indexes(built: list[BuiltItem]) -> dict[str, dict[str, list[str]]]:
    idx: dict[str, dict[str, list[str]]] = {
        k: defaultdict(list) for k in ("FTR", "ROL", "ENT", "FLW", "INT")
    }
    for b in built:
        for a in b.item.applies_to:
            if a.kind in idx and a.id and b.item.id not in idx[a.kind][a.id]:
                idx[a.kind][a.id].append(b.item.id)
    return {k: {i: sorted(v) for i, v in sorted(d.items())} for k, d in idx.items()}


def _gaps(ctx: RunContext, built: list[BuiltItem], reason: str | None) -> list[Gap]:
    gaps: list[Gap] = []
    produced = {b.item.target_id for b in built} | {t for b in built for t in b.target_ids}
    for ts in sorted(ctx.targets.values(), key=lambda s: s.target.id):
        t = ts.target
        if ts.status == "covered":
            continue
        if ts.status == "cancelled":
            gr: GapReason = "user_cancelled"
        elif ts.status == "not_found":
            gr = "not_found"
        elif ts.reason == "provider_down":
            gr = "provider_down"
        elif ts.reason in ("budget_stop",) or ctx.budget_stopped:
            gr = "budget_stop"
        elif ts.status in ("queued", "running") or (ts.reason == "skipped" and not ts.queries):
            gr = "not_run"
        else:
            gr = "not_found" if t.id not in produced else "budget_stop"
        gaps.append(Gap(target_id=t.id, reason=gr, label=t.label, queries=ts.queries[:6]))
    for t in ctx.plan.unrun:
        gaps.append(Gap(target_id=t.id, reason="not_run", label=t.label))
    for d in ctx.plan.dropped:
        gaps.append(
            Gap(target_id=f"D-{clean_key(d.label)[:20]}", reason=_drop_reason(d), label=d.label)
        )
    for r in ctx.input.references:
        if r.own_brand:
            gaps.append(
                Gap(
                    target_id=f"R-{clean_key(r.label)[:20]}",
                    reason="ambiguous_reference",
                    label=r.label,
                )
            )
    # RV-10: every trusted request appears in an item or a gap
    covered_requests = {rid for b in built for rid in b.item.request_ids}
    linked = {rid for ts in ctx.targets.values() for rid in ts.target.links.requests}
    for r in ctx.input.requests:
        if r.origin == "template" or r.thin:
            continue
        if r.id not in covered_requests and not any(
            g.label and r.id in ts.target.links.requests
            for ts in ctx.targets.values()
            for g in gaps
            if g.target_id == ts.target.id
        ):
            gaps.append(
                Gap(
                    target_id=f"RR-{r.id}",
                    reason="not_run" if r.id not in linked else "not_found",
                    label=r.topic[:80],
                )
            )
    return gaps


def _drop_reason(d: Dropped) -> GapReason:
    return {
        "declined": "declined",
        "trivia": "not_worthwhile",
        "own_brand": "ambiguous_reference",
        "over_budget": "not_run",
    }.get(d.reason, "not_worthwhile")  # type: ignore[return-value]


def _model_of(ctx: RunContext, prefix: str) -> str:
    """The model that answered the calls whose spec id starts with ``prefix`` (the first non-empty one)."""
    return next((r.model for cid, r in ctx.runner.calls if cid.startswith(prefix) and r.model), "")


def _fallbacks(ctx: RunContext) -> dict[str, int]:
    """How often each call fell back to its deterministic path (never invented content; shown in the report)."""
    out: dict[str, int] = defaultdict(int)
    for cid, r in ctx.runner.calls:
        if r.used_fallback:
            out[cid] += 1
    return dict(sorted(out.items()))


def _target_infos(ctx: RunContext) -> list[TargetInfo]:
    return [
        TargetInfo(ts.target.job, ts.target.lane, ts.status, ts.slots_required, ts.slots_filled)
        for ts in sorted(ctx.targets.values(), key=lambda s: s.target.id)
    ]


async def _model_order(
    ctx: RunContext, lane_items: dict[str, list[BuiltItem]], limits: dict[str, int], use_model: bool
) -> dict[str, tuple[list[str], dict[str, str]]]:
    inp = asm_call.AssembleIn(
        language="en",
        lanes=[
            asm_call.LaneIn(
                lane=lane,
                limit=limits[lane],
                items=[
                    asm_call.ItemIn(
                        id=b.item.id,
                        kind=b.item.kind,
                        label=b.item.label,
                        claim=b.item.claim,
                        level=b.item.prevalence.level if b.item.prevalence else "limited_evidence",
                        impact=b.item.impact,
                        flags=list(b.item.flags),
                    )
                    for b in items[: limits[lane] * 2]
                ],
            )
            for lane, items in lane_items.items()
            if items
        ],
    )
    if use_model and inp.lanes:
        with ctx.stage("assemble"):
            res = await ctx.runner.run(asm_call.SPEC, inp)
        ctx.budget.add_llm_cost(res.cost_micros, "assemble")
        if res.failed:
            ctx.failures.record("llm", res.reason or "failed")
            await ctx.warn("model_failed")
        out = res.output
    else:
        out = asm_call.deterministic_assembly(inp)
    result: dict[str, tuple[list[str], dict[str, str]]] = {}
    by_id = {b.item.id: b for items in lane_items.values() for b in items}
    default = asm_call.default_notes()
    for lane in out.lanes:
        offered = {i.id for ln in inp.lanes if ln.lane == lane.lane for i in ln.items}
        order = [i for i in lane.order if i in offered][: limits.get(lane.lane, 50)]
        notes: dict[str, str] = {}
        for n in lane.notes:
            b = by_id.get(n.id)
            if b is None or n.id not in order:
                continue
            words = n.use_note.split()
            source_text = [b.item.label, b.item.claim, json.dumps(b.item.data, ensure_ascii=False)]
            if len(words) > MAX_NOTE_WORDS or novel_tokens(n.use_note, source_text, openers()):
                notes[n.id] = default.get(b.item.kind, "")
            else:
                notes[n.id] = n.use_note.strip()
        result[lane.lane] = (order, notes)
    return result


async def assemble_output(
    ctx: RunContext,
    result: DirectorResult,
    *,
    use_model: bool = True,
    started_at: Any = None,
    finished_at: Any = None,
) -> tuple[ResearchOutput, dict[str, Any]]:
    cat = ctx.catalog
    defaults = _defaults_data()
    index = BriefIndex.from_input(ctx.input, list(cat.needs.needs))
    built = build_items(ctx, index)
    targets = {tid: ts.target for tid, ts in ctx.targets.items()}
    drops = DroppedCounts(
        **{
            k: getattr(ctx.drops, k)
            for k in (
                "quote_not_found",
                "number_not_in_quote",
                "not_supported",
                "unsupported_field",
                "forbidden_pattern",
                "claim_too_long",
                "invalid",
            )
        }
    )

    klass = ctx.input.load.load_class if ctx.input.load else "standard"
    cs = cat.ceilings.for_class(klass)
    norms = compute_norms(built, ctx.input, cat.prevalence, ctx.as_of)
    built = apply_norms(built, norms)
    conflicts = detect_conflicts(built, ctx.input)
    confirm = _confirm(built, defaults["confirm_reasons"])
    suggestions = build_suggestions(built, norms)
    default_notes = asm_call.default_notes()

    # --- the main store and the appendix (AD-55): nothing is deleted to fit a size -------------------------------
    sources_table = {s.id: s.model_dump(mode="json") for b in built for s in b.item.sources}
    overhead = tokens_of(
        [
            [n.model_dump(mode="json") for n in norms],
            suggestions.model_dump(mode="json"),
            [c.model_dump(mode="json") for c in confirm],
            [c.model_dump(mode="json") for c in conflicts],
            sources_table,
        ]
    )
    splan = assign_store(
        [b.item.model_dump(mode="json") for b in built],
        overhead_tokens=overhead,
        store_tokens=cs.store_tokens,
        default_notes=default_notes,
        note_allowance=NOTE_TOKEN_ALLOWANCE,
    )
    if splan.protected_over_limit:
        await ctx.warn("store_over_limit")
    for b in built:
        d = splan.decisions[b.item.id]
        b.item = b.item.model_copy(
            update={
                "rank": d.rank,
                "tokens_est": d.tokens_est,
                "store": d.store,
                "appendix_reason": d.appendix_reason,
            }
        )

    # --- lane membership (main store only) and the model's ordering ----------------------------------------------
    main = [b for b in built if b.item.store == "main"]
    lane_items: dict[str, list[BuiltItem]] = {lane: [] for lane in cat.lanes.lanes}
    for b in main:
        for lane in _lanes_of(b, ctx, targets):
            if lane in lane_items:
                lane_items[lane].append(b)
    for b in main:
        if (
            any(f in b.item.flags for f in ("needs-confirm", "legal-adjacent"))
            and b not in lane_items["verify"]
        ):
            lane_items["verify"].append(b)
    for lane in lane_items:
        lane_items[lane].sort(key=lambda b: b.item.rank)
    digest_cap_chars = cs.digest_tokens * dg.CHARS_PER_TOKEN
    limits = {lane: max(10, cs.digest_tokens // 60) for lane in lane_items}
    selection = await _model_order(
        ctx, lane_items, limits, use_model and not ctx.run_cancel.cancelled
    )
    by_id = {b.item.id: b for b in built}
    for _lane, (_order, notes) in selection.items():
        for iid, note in notes.items():
            if iid in by_id and note and not by_id[iid].item.use_note:
                by_id[iid].item = by_id[iid].item.model_copy(update={"use_note": note})
    for b in built:  # every item gets its kind's default note when the model wrote none
        if not b.item.use_note:
            b.item = b.item.model_copy(update={"use_note": default_notes.get(b.item.kind, "")})
    for b in built:  # the notes are in: the final size of every item's compact view
        d = b.item.model_dump(mode="json")
        b.item = b.item.model_copy(update={"tokens_est": tokens_of(compact_item(d, default_notes))})
    gaps = _gaps(ctx, built, result.reason)
    defaults_proposed = _defaults(built, ctx, defaults["defaults"])
    ordered: dict[str, list[BuiltItem]] = {}
    for lane in lane_items:
        first = [i for i in selection.get(lane, ([], {}))[0] if i in by_id]
        rest = [b.item.id for b in lane_items[lane] if b.item.id not in set(first)]
        ordered[lane] = [by_id[i] for i in [*first, *rest]]
    slices: dict[str, Slice] = {}
    digests = {
        "blueprint": dg.blueprint_digest(ordered["blueprint"], norms, digest_cap_chars),
        "integrations": dg.integrations_digest(ordered["integrations"], norms, digest_cap_chars),
        "design": dg.design_digest(ordered["design"], norms, digest_cap_chars),
        "screens": dg.screens_digest(ordered["screens"], norms, digest_cap_chars),
        "mock": dg.mock_digest(ordered["mock"], norms, digest_cap_chars),
        "verify": dg.verify_digest(main, confirm, defaults_proposed, gaps, digest_cap_chars),
    }
    for lane in cat.lanes.lanes:
        ids = [b.item.id for b in ordered.get(lane, [])]
        slices[lane] = Slice(
            item_ids=ids,
            chunks=[ids[i : i + cs.chunk_items] for i in range(0, len(ids), cs.chunk_items)],
            digest=digests.get(lane, ""),
        )
    idx = _indexes(built)
    states = [ts.status for ts in ctx.targets.values()]
    required = sum(ts.slots_required for ts in ctx.targets.values())
    filled = sum(ts.slots_filled for ts in ctx.targets.values())
    load = ctx.input.load
    iq = ctx.input.quality.model_dump(mode="json")
    iq["plan_source"] = ctx.plan.plan_source
    usd = Decimal(micros_to_usd(ctx.budget.usd_micros))
    output = ResearchOutput(
        program_id=ctx.input.program_id,
        brief_hash=ctx.input.brief_hash,
        status=result.status,  # type: ignore[arg-type]
        profile=Profile(
            load_score=load.score if load else 0,
            load_class=load.load_class if load else "standard",
            rounds=result.rounds,
            light=ctx.plan.light,
            signals=list(ctx.plan.signals),
            store_tokens=overhead
            + sum(b.item.tokens_est for b in built if b.item.store == "main"),
            appendix_items=sum(1 for b in built if b.item.store == "appendix"),
            store_over_limit=splan.protected_over_limit,
            digest_tokens=sum(tokens_of(s.digest) for s in slices.values()),
        ),
        input_quality=iq,
        budget=Budget(
            searches_used=ctx.budget.searches_used,
            pages_read=ctx.budget.pages_read,
            seconds=int(ctx.budget.elapsed()),
            usd=format(usd.quantize(Decimal("0.0001")), "f"),
            usd_micros=ctx.budget.usd_micros,
            failures=ctx.failures.total,
        ),
        coverage=Coverage(
            targets=len(ctx.targets),
            covered=sum(1 for s in states if s == "covered"),
            not_found=sum(1 for s in states if s == "not_found"),
            slots_required=required,
            slots_filled=filled,
        ),
        by_feature=idx["FTR"],
        by_role=idx["ROL"],
        by_entity=idx["ENT"],
        by_flow=idx["FLW"],
        by_integration=idx["INT"],
        items=[b.item for b in built],
        norms=norms,
        suggestions=suggestions,
        defaults_proposed=defaults_proposed,
        confirm=confirm,
        gaps=gaps,
        conflicts=conflicts,
        slices=slices,
        claims_used=[],
        dropped=drops,
        flagged=flagged_counts([b.item for b in built]),
        lane_quality=lane_quality(
            lane_items_of([b.item for b in built], cat.lanes, {tid: [t.lane, *t.also_lanes] for tid, t in targets.items()}),
            _target_infos(ctx),
            list(cat.lanes.lanes),
        ),
        bn_coverage=bn_coverage([b.item for b in built], _target_infos(ctx), cat.lanes),
        run=RunInfo(
            run_id=ctx.run_id,
            started_at=started_at,
            finished_at=finished_at,
            as_of=ctx.as_of,
            catalog_hash=cat.catalog_hash,
            checker_vendor=ctx.checker_vendor
            if ctx.checker_vendor in ("same", "different")
            else "none",  # type: ignore[arg-type]
            search_provider=ctx.search.name,
            plan_source=ctx.plan.plan_source,
            notes=list(result.notes),
            writer_model=_model_of(ctx, "rsch.extract"),
            checker_model=_model_of(ctx, "rsch.check"),
            stages={k: round(v, 1) for k, v in sorted(ctx.timings.items())},
            cost_by_step=dict(sorted(ctx.budget.by_step.items())),
            fallbacks=_fallbacks(ctx),
        ),
    )
    out_dict = output.to_json_dict()
    out_dict["schema"] = SCHEMA_VERSION
    evidence = build_evidence(ctx, built, out_dict)
    return output, evidence


def build_evidence(
    ctx: RunContext, built: list[BuiltItem], research: dict[str, Any]
) -> dict[str, Any]:
    """The internal file: fetch log, stored page text, extractions with quotes, queries, failures. Never read downstream."""
    item_of_atom = {a.id: b.item.id for b in built for a in b.atoms}
    return {
        "schema": "research.evidence.v1",
        "run_id": ctx.run_id,
        "brief_hash": ctx.input.brief_hash,
        "content_hash": content_hash(research),
        "pages": [
            {
                "id": p.id,
                "url": p.url,
                "final_url": p.final_url,
                "domain": p.domain,
                "source_class": p.source_class,
                "title": p.title,
                "published": p.published.isoformat() if p.published else None,
                "retrieved_on": p.retrieved_on.isoformat(),
                "text_source": p.text_source,
                "source_language": p.language,
                "content_hash": p.content_hash,
                "text": p.text,
            }
            for p in sorted(ctx.pages.values(), key=lambda x: x.url)
        ],
        "queries": [
            {
                "id": q.id,
                "target_id": q.target_id,
                "round": q.round,
                "text": q.text,
                "variant": q.variant,
                "language": q.language,
                "country": q.country,
                "provider": q.provider,
                "exact": q.exact,
                "domain": q.domain,
                "results": q.results,
                "urls": q.urls,
                "failed": q.failed,
                "cost_micros": q.cost_micros,
                "applied": q.applied,
                "locale_weak": q.locale_weak,
            }
            for q in ctx.queries
        ],
        "atoms": [
            {
                "id": a.id,
                "item_id": item_of_atom.get(a.id),
                "kind": a.kind,
                "job": a.job,
                "target_id": a.target_id,
                "norm_key": a.norm_key,
                "label": a.label,
                "claim": a.claim,
                "quote": a.quote,
                "impact": a.impact,
                "page_id": a.page_id,
                "status": a.status,
                "reject_reason": a.reject_reason,
                "data": a.data,
                "verdict": a.verdict,
                "checked": a.checked,
                "flags": sorted(a.flags),
                "about_region": a.about_region,
                "about_entity": a.about_entity,
                "unsupported_fields": a.unsupported_fields,
            }
            for ts in sorted(ctx.targets.values(), key=lambda s: s.target.id)
            for a in ts.atoms
        ],
        "targets": [
            {
                "id": ts.target.id,
                "label": ts.target.label,
                "job": ts.target.job,
                "lane": ts.target.lane,
                "also_lanes": list(ts.target.also_lanes),
                "place": ts.target.place.country_code if ts.target.place else None,
                "entity": ts.target.entity,
                "status": ts.status,
                "reason": ts.reason,
                "slots_required": ts.slots_required,
                "slots_filled": ts.slots_filled,
                "queries": ts.queries,
                "followups": ts.followups,
            }
            for ts in sorted(ctx.targets.values(), key=lambda s: s.target.id)
        ],
        "entities": [
            {"name": e.name, "kind": e.kind, "domain": e.domain}
            for e in sorted(ctx.entities.values(), key=lambda e: e.name)
        ],
        "failures": ctx.failures.as_list(),
        "extraction": dict(sorted(ctx.extraction.items())),
    }
