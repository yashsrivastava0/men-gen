"""From the fact ledger to the Brief (build prompt 02, sections 3.3 and 3.4; ADR 0015; ADR 0016 v2).

At the start of Brief review the active ledger is compiled (``cmp.compile_brief``). v2 runs ONE CALL PER SECTION GROUP,
in parallel (``fact_kinds.yaml``, ``compile_groups``): a call gets the facts routed to its group, the project card and
the Brief items that already exist, and returns values, list elements to add, typed placements; one more call drafts
typical details. In code, whatever the model said:

* a value never overwrites what the person gave; a guess is replaced only by verified evidence; a list slot is EXTENDED
  with elements that cite an active fact, share a real word with it and are new by name (``engine.compile_apply``);
* a Locked item needs verified quotes (``Fact.supports_locked``); anything else is Default; a Locked value that cites a
  fact that is gone is downgraded first (``engine.lock_guard``), so nothing Locked rests on a superseded fact;
* every active fact ends up in ``domain_facts`` (typed, with the records it belongs to), is the evidence of a written
  value, or is marked ``not_used`` with a reason. A fact nobody accounted for is UNPLACED: the engine asks once more,
  then places it itself and counts it (``meta.interview.facts_unreferenced``), so the coverage metric is honest;
* a call that fails is the deterministic floor for ITS section only (flagged and counted); the others are unaffected;
* a section whose facts did not change since the last run is not compiled again (``meta.compile_digests``), so a re-run
  with the same ledger changes nothing and the Brief hash does not churn;
* superseded and disputed facts become correction assumptions and typical details become assumptions (Gate 1).

Nothing here is chosen by a model beyond the values and placements it proposes; tiers, coverage and the final shape of
the section are computed by code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING

from mde_compass.domain.brief import DomainFact
from mde_compass.domain.interview import Fact
from mde_compass.engine import assumptions as assumptions_mod
from mde_compass.engine import facts as facts_mod
from mde_compass.engine.compile_apply import PROVENANCE_BY_ORIGIN as PROVENANCE_BY_ORIGIN
from mde_compass.engine.compile_apply import (
    ApplyReport,
    Merged,
    apply_values,
    detail_row,
    merge_results,
)
from mde_compass.engine.compile_apply import domain_fact as domain_fact
from mde_compass.engine.compile_apply import provenance_of as provenance_of
from mde_compass.engine.compile_plan import (
    TYPICAL_KEY,
    CallPlan,
    existing_lines,
    group_paths,
    plan_calls,
    project_card,
    slot_state,
    stale_keys,
    typical_digest,
)
from mde_compass.engine.compile_types import (
    NO_TARGETS,
    DetailPlacement,
    SectionCompiler,
    SectionRequest,
    SectionResult,
    floor_result,
    from_basic,
)
from mde_compass.engine.context_pack import full_ledger
from mde_compass.engine.derived_assumptions import corrections_from_facts, typical_assumptions
from mde_compass.engine.facts import digest_line
from mde_compass.engine.lock_guard import downgrade_unsupported_locks
from mde_compass.engine.ports import BriefCompiler, CompileRequest, TurnContext
from mde_core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from mde_compass.engine.interview import InterviewController
    from mde_compass.engine.service import _Turn

log = get_logger(__name__)

COMPILE_BATCH_CHARS = 30_000
"""Ledger characters per call: two wire pieces of at most 15,000, far below the gateway's 20,000 per piece."""
TYPICAL_LEDGER_CHARS = 40_000
MAX_ASSUMPTION_LINES = 80
SECOND_PASS_GROUP = "place"  # noqa: S105 (a compile group id, not a secret)


def referenced_by_slots(turn: _Turn) -> set[str]:
    """Fact ids cited by a slot's evidence (the projector writes ``ref = FCT-###``)."""
    refs: set[str] = set()
    for rec in turn.slots.all().values():
        for prov in rec.provenance:
            if prov.ref.startswith("FCT-"):
                refs.add(prov.ref)
    return refs


async def _run(
    compiler: BriefCompiler, ctx: TurnContext, request: SectionRequest, facts: Sequence[Fact]
) -> SectionResult:
    """One call. The real adapter is a ``SectionCompiler``; a compiler that only has the basic port (a test double,
    the unavailable placeholder) is called through it. An exception, not only a flagged fallback, is the floor for
    this section alone."""
    try:
        if isinstance(compiler, SectionCompiler):
            return await compiler.compile_section(ctx, request)
        if request.task == "typical":
            return SectionResult()
        compilation = await compiler.compile_brief(
            ctx,
            CompileRequest(
                language=request.language,
                ledger=request.ledger,
                assumptions=list(request.assumptions),
                sections=[s for s in request.sections if s != NO_TARGETS] or None,
            ),
        )
        return from_basic(compilation, facts)
    except Exception as exc:
        log.warning("compile_section_failed", group=request.group, error=type(exc).__name__)
        return floor_result(facts) if request.task == "fill" else SectionResult(used_fallback=True)


def _typical_ledger(active: Sequence[Fact]) -> str:
    """What the person already told us, one short line per fact (no quotes); the oldest are dropped when it is long."""
    lines = [digest_line(f) for f in active]
    while lines and sum(len(line) + 1 for line in lines) > TYPICAL_LEDGER_CHARS:
        lines.pop(0)
    return "\n".join(lines)


def _accounted(merged: Merged, report: ApplyReport, slot_refs: set[str]) -> set[str]:
    """Facts somebody accounted for: placed, the evidence of a written value, not used, or cited by a slot."""
    return set(merged.placements) | set(merged.not_used) | report.applied | slot_refs


async def compile_ledger(ctl: InterviewController, turn: _Turn) -> None:
    """Compile the ledger into the Brief at review (planner engine only)."""
    svc = ctl.svc
    assert svc.ports is not None
    mem = turn.memory
    now = svc.clock.now()
    active = mem.active_facts()
    cfg = svc.config.context
    catalog = svc.catalog
    ctx = svc._ctx(turn)
    compiler = svc.ports.brief_compiler
    digests = dict(turn.brief.meta.compile_digests)

    # 0. nothing Locked may rest on a fact that is gone (a pivot or an edited answer superseded it): downgrade first, so a
    # compile with verified evidence can replace the guess
    for change in downgrade_unsupported_locks(turn.slots, mem.facts):
        log.info(
            "lock_downgraded",
            slot=change.slot_id,
            facts=list(change.unsupported),
            records=change.records,
        )

    # 1. what each call is told: the facts of its group, the project card, what the Brief already holds
    project = project_card(turn.slots, catalog)
    existing = existing_lines(turn.brief)
    guesses = [a.claim for a in turn.brief.assumptions if a.slot_id][:MAX_ASSUMPTION_LINES]
    plans = plan_calls(active, max_facts=cfg.compile_chunk_facts, max_chars=COMPILE_BATCH_CHARS)
    todo = [p for p in plans if digests.get(p.key) != p.digest]
    cached_ids = {f.id for p in plans if p not in todo for f in p.facts}

    def request_for(plan: CallPlan) -> SectionRequest:
        return SectionRequest(
            language=turn.lang,
            task="fill",
            group=plan.group,
            sections=list(plan.sections),
            ledger=full_ledger(plan.facts),
            project=project,
            existing=existing,
            slot_state=slot_state(turn.slots, group_paths(catalog, plan.sections)),
            assumptions=guesses,
        )

    typical_key = typical_digest(active)
    run_typical = bool(active) and digests.get(TYPICAL_KEY) != typical_key
    typical_request = SectionRequest(
        language=turn.lang,
        task="typical",
        group=TYPICAL_KEY,
        ledger=_typical_ledger(active),
        project=project,
        existing=existing,
        assumptions=[a.claim for a in turn.brief.assumptions][:MAX_ASSUMPTION_LINES],
    )

    # 2. the calls, in parallel: one failing never affects the others
    jobs = [_run(compiler, ctx, request_for(p), p.facts) for p in todo]
    if run_typical:
        jobs.append(_run(compiler, ctx, typical_request, ()))
    done = list(await asyncio.gather(*jobs)) if jobs else []
    fill_results = done[: len(todo)]
    typical_result = done[len(todo)] if run_typical else None

    # 3. values and list elements into the slots; then one more ask for the facts nobody accounted for
    merged = merge_results(fill_results)
    report = apply_values(merged, turn.slots, catalog, mem.fact, now=now, turn_id=turn.turn_id)
    fresh = {
        f.id: f
        for p, r in zip(todo, fill_results, strict=True)
        if not r.used_fallback
        for f in p.facts
    }
    slot_refs = referenced_by_slots(turn)
    missing = [f for f in fresh.values() if f.id not in _accounted(merged, report, slot_refs)]
    second: list[SectionResult] = []
    if missing:
        size = max(1, cfg.compile_chunk_facts)
        batches = [missing[i : i + size] for i in range(0, len(missing), size)]
        second = list(
            await asyncio.gather(
                *(
                    _run(
                        compiler,
                        ctx,
                        SectionRequest(
                            language=turn.lang,
                            task="fill",
                            group=SECOND_PASS_GROUP,
                            sections=[NO_TARGETS],
                            ledger=full_ledger(batch),
                            project=project,
                            existing=existing,
                        ),
                        batch,
                    )
                    for batch in batches
                )
            )
        )
        merged = merge_results([*fill_results, *second])
        second_report = apply_values(
            merged, turn.slots, catalog, mem.fact, now=now, turn_id=turn.turn_id
        )
        report.applied |= second_report.applied
        report.written += [s for s in second_report.written if s not in report.written]
    failed = merged.failed + (
        1 if typical_result is not None and typical_result.used_fallback else 0
    )
    if failed:
        ctl.count_fallback(turn, failed)

    # 4. not_used: small talk and the like, each with a reason
    for n in merged.not_used.values():
        fact = mem.fact(n.fact_id)
        if fact is not None and fact.status == "active" and n.reason.strip():
            mem.update_fact(facts_mod.mark_not_used(fact, n.reason.strip(), now))

    # 5. coverage: placed, written, cached or not_used; what is none of these is placed by code and counted
    still_active = mem.active_facts()
    slot_refs = referenced_by_slots(turn)
    referenced = _accounted(merged, report, slot_refs) | cached_ids
    leftovers = set(facts_mod.fact_coverage(still_active, referenced).unreferenced)

    svc._rebuild(turn)  # records get their ids, so refs can point at them
    brief = turn.brief
    old_rows = {r.id: r for r in brief.domain_facts}
    rows: list[DomainFact] = []
    for fact in still_active:
        placement = merged.placements.get(fact.id)
        if placement is None and fact.id in leftovers:
            placement = DetailPlacement(
                fact_id=fact.id, topic=fact.topics[0] if fact.topics else fact.type
            )
        if placement is None and fact.id not in old_rows:
            continue  # the evidence of a written value: no separate row needed
        rows.append(detail_row(fact, placement, old_rows.get(fact.id), brief))
    brief.domain_facts = sorted(rows, key=lambda r: r.id)

    # 6. assumptions that stand for no slot: corrections (code only) and typical details
    brief = assumptions_mod.merge_derived(
        brief, corrections_from_facts(mem.facts, catalog, turn.lang), ("correction",)
    )
    if typical_result is not None and not typical_result.used_fallback:
        brief = assumptions_mod.merge_derived(
            brief,
            typical_assumptions(typical_result.typical, brief, catalog, turn.lang),
            ("typical",),
        )
    turn.brief = brief

    # 7. bookkeeping: what each successful call was built from (a failed call or an unplaced fact retries next time)
    ok = [
        p
        for p, r in zip(todo, fill_results, strict=True)
        if not r.used_fallback and not any(f.id in leftovers for f in p.facts)
    ]
    kept = {k: v for k, v in digests.items() if k not in stale_keys(digests, plans)}
    kept.update({p.key: p.digest for p in ok})
    if typical_result is not None and not typical_result.used_fallback:
        kept[TYPICAL_KEY] = typical_key
    turn.brief.meta.compile_digests = kept
    block = ctl.block(turn)
    total = max(len(still_active), 1)
    turn.brief.meta.interview = block.model_copy(
        update={
            "facts_unreferenced": len(leftovers),
            "coverage": round(1.0 - len(leftovers) / total, 4) if still_active else 1.0,
        }
    )
    if merged.summary and not turn.brief.summary:
        turn.brief.summary = merged.summary
    if merged.pitch and not turn.brief.pitch:
        turn.brief.pitch = merged.pitch
    ctl.touch(turn)
    svc._rebuild(turn)
