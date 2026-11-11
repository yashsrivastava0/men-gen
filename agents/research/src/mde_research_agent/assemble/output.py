"""Assemble the committed ``research.json`` (``research.v4``) and the internal ``research_evidence`` from a finished run.

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
        elif ts.status in ("queued", "running"):
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


def _est_tokens(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str)) // 4


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
        res = await ctx.runner.run(asm_call.SPEC, inp)
        ctx.budget.add_llm_cost(res.cost_micros)
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
            if len(words) > MAX_NOTE_WORDS or novel_tokens(n.use_note, source_text):
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
                "not_supported",
                "size_cap",
                "forbidden_pattern",
                "unchecked_high_impact",
            )
        }
    )

    cap_tokens = cat.ceilings.for_class(
        ctx.input.load.load_class if ctx.input.load else "standard"
    ).hard.research_tokens
    while True:
        norms = compute_norms(built, ctx.input, cat.prevalence, ctx.as_of)
        built = apply_norms(built, norms)
        conflicts = detect_conflicts(built)
        confirm = _confirm(built, defaults["confirm_reasons"])
        suggestions = build_suggestions(built, norms)
        probe = [b.item.model_dump(mode="json") for b in built]
        if (
            _est_tokens(probe) + _est_tokens([n.model_dump(mode="json") for n in norms])
            <= cap_tokens
            or len(built) <= 5
        ):
            break

        # RV-09: drop the lowest-ranked items first, a share of every kind so no kind disappears, and count them
        def quality(b: BuiltItem) -> tuple[int, int, int, str]:
            level = b.item.prevalence.level if b.item.prevalence else "limited_evidence"
            return (
                LEVEL_RANK.get(level, 5),
                0 if b.item.impact == "high" else 1,
                1 if "single-source" in b.item.flags else 0,
                b.item.id,
            )

        by_kind: dict[str, list[BuiltItem]] = defaultdict(list)
        for b in built:
            by_kind[b.item.kind].append(b)
        tails: list[tuple[float, str, BuiltItem]] = []
        for members in by_kind.values():
            ranked = sorted(members, key=quality)
            for pos, b in enumerate(ranked):
                if pos >= 2:  # always keep the best two of every kind
                    tails.append((pos / len(ranked), b.item.id, b))
        tails.sort(key=lambda t: (-t[0], t[1]))
        cut = max(1, len(built) // 10)
        remove = {b.item.id for _, _, b in tails[:cut]}
        if not remove:
            break
        drops.size_cap += len(remove)
        built = [b for b in built if b.item.id not in remove]
    # lane membership and selection
    lane_items: dict[str, list[BuiltItem]] = {lane: [] for lane in cat.lanes.lanes}
    for b in built:
        for lane in _lanes_of(b, ctx, targets):
            if lane in lane_items:
                lane_items[lane].append(b)
    for b in built:
        if (
            any(f in b.item.flags for f in ("needs-confirm", "legal-adjacent"))
            and b not in lane_items["verify"]
        ):
            lane_items["verify"].append(b)
    slice_cap = cat.ceilings.for_class(
        ctx.input.load.load_class if ctx.input.load else "standard"
    ).soft.slice_tokens
    limits = {lane: max(10, slice_cap // 60) for lane in lane_items}
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
            b.item = b.item.model_copy(
                update={"use_note": asm_call.default_notes().get(b.item.kind, "")}
            )
    gaps = _gaps(ctx, built, result.reason)
    defaults_proposed = _defaults(built, ctx, defaults["defaults"])
    cap_chars = slice_cap * dg.CHARS_PER_TOKEN
    ordered: dict[str, list[BuiltItem]] = {}
    for lane in lane_items:
        order_ids = (
            selection.get(lane, ([], {}))[0]
            or [b.item.id for b in lane_items[lane]][: limits[lane]]
        )
        ordered[lane] = [by_id[i] for i in order_ids if i in by_id]
    slices: dict[str, Slice] = {}
    digests = {
        "blueprint": dg.blueprint_digest(ordered["blueprint"], norms, cap_chars),
        "integrations": dg.integrations_digest(ordered["integrations"], norms, cap_chars),
        "design": dg.design_digest(ordered["design"], norms, cap_chars),
        "screens": dg.screens_digest(ordered["screens"], norms, cap_chars),
        "mock": dg.mock_digest(ordered["mock"], norms, cap_chars),
        "verify": dg.verify_digest(built, confirm, defaults_proposed, gaps, cap_chars),
    }
    for lane in cat.lanes.lanes:
        slices[lane] = Slice(
            item_ids=[b.item.id for b in ordered.get(lane, [])], digest=digests.get(lane, "")
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
                "verdict": a.verdict,
                "checked": a.checked,
                "flags": sorted(a.flags),
            }
            for ts in sorted(ctx.targets.values(), key=lambda s: s.target.id)
            for a in ts.atoms
        ],
        "targets": [
            {
                "id": ts.target.id,
                "label": ts.target.label,
                "job": ts.target.job,
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
    }
