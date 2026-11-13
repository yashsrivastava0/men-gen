"""Plan, validate, audit, repair once, drop: how a planner batch becomes the questions that may be shown.

The model proposes; code decides. In order:

1. ``cmp.plan_questions`` returns a batch (or its flagged fallback, which skips the rest: those cards are made by
   code from the Needs Registry).
2. Code validators (``question_validators``) reject what is generic, repeated, jargon-heavy, malformed, off
   language, a technology question or outside the capabilities.
3. ``cmp.audit_questions`` reviews the survivors; a question scoring below the policy minimum, or carrying any
   flag, is rejected.
4. Rejected questions and uncovered must/high-impact needs earn ONE repair call to the planner. Whatever is still
   rejected is dropped. A canned question is never substituted silently.
5. Only when nothing at all survives in a mode that must ask does the engine ask the planner port for its flagged
   fallback batch (counted: a fallback is a failure).
6. The batch is cut to the wanted size, keeping the planner's most important questions (its priority, 1 to 5,
   ties in the planner's own order) in the planner's reading order; extras go to the backlog.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from mde_compass.catalog import Catalog
from mde_compass.config import InterviewPolicy
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import (
    AuditVerdict,
    BacklogItem,
    BatchGroup,
    NeedStatus,
    PlannedBatch,
    RejectedQuestion,
    stored_mode,
)
from mde_compass.engine.ports import (
    AuditRequest,
    PlanRequest,
    QuestionAuditor,
    QuestionPlanner,
    TurnContext,
)
from mde_compass.engine.question_validators import (
    LedgerEntry,
    ValidationContext,
    in_original_order,
    rank_by_priority,
    validate_batch,
)

ASKING_MODES = ("initial", "replan", "deepen")
"""Modes that must put something to the person when needs are open (follow-up and simplify may ask nothing:
the engine then assumes visibly)."""


@dataclass
class PipelineResult:
    batch: PlannedBatch
    rejected: list[RejectedQuestion] = field(default_factory=list)
    verdicts: list[AuditVerdict] = field(default_factory=list)
    used_fallback: bool = False  # the planner's flagged fallback batch is what is returned
    audit_fallback: bool = False  # the auditor ran on its deterministic heuristic
    repaired: bool = False
    planner_calls: int = 0
    uncovered: list[str] = field(
        default_factory=list
    )  # must/high-impact needs still unaccounted for
    reason: str = ""

    @property
    def fallbacks(self) -> int:
        """How many fallbacks this run used (a fallback is a failure and is counted)."""
        return int(self.used_fallback) + int(self.audit_fallback)


def audit_reasons(verdict: AuditVerdict, policy: InterviewPolicy) -> list[str]:
    """Why the auditor's verdict rejects a question (empty means it passes)."""
    reasons: list[str] = []
    if verdict.specific < policy.audit_min_specificity:
        reasons.append(f"specificity {verdict.specific} is below {policy.audit_min_specificity}")
    if verdict.leading:
        reasons.append("leading wording")
    if verdict.jargon:
        reasons.append("jargon")
    if verdict.duplicate_of:
        reasons.append(f"repeats {verdict.duplicate_of}")
    if not verdict.answerable:
        reasons.append("this person could not answer it")
    if not verdict.maps_to_need:
        reasons.append("it serves no need")
    return reasons


def _numbers(cards: list[Card]) -> int:
    best = 0
    for card in cards:
        tail = card.id.rsplit("-", 1)[-1]
        if tail.isdigit():
            best = max(best, int(tail))
    return best


def uncovered_obligations(
    batch_cards: list[Card],
    covered: list[str],
    assumed: list[str],
    obligations: list[str],
) -> list[str]:
    """Must-level or high-impact needs the batch neither asks, nor lists as covered, nor assumes instead."""
    asked = {need for card in batch_cards for need in card.need_ids}
    accounted = asked | set(covered) | set(assumed)
    return [need for need in obligations if need not in accounted]


async def _screen(
    *,
    cards: list[Card],
    vctx: ValidationContext,
    auditor: QuestionAuditor,
    ctx: TurnContext,
    request: PlanRequest,
    needs_table: str,
    asked_list: str,
    policy: InterviewPolicy,
    result: PipelineResult,
) -> tuple[list[Card], list[RejectedQuestion]]:
    """Code validators, then the auditor, on one set of cards."""
    if not cards:
        return [], []
    # the most important questions are judged first, so a batch that is too big loses the least important ones
    code = validate_batch(rank_by_priority(cards), vctx)
    accepted = in_original_order(code.accepted, cards)  # the person still reads the planner's order
    rejected = list(code.rejected)
    if not accepted:
        return [], rejected
    audit = await auditor.audit(
        ctx,
        AuditRequest(
            language=request.language,
            cards=accepted,
            project_terms=list(request.project_terms),
            needs_table=needs_table,
            asked_list=asked_list,
        ),
    )
    if audit.used_fallback:
        result.audit_fallback = True
    verdicts = {v.card_id: v for v in audit.verdicts}
    result.verdicts.extend(audit.verdicts)
    kept: list[Card] = []
    for card in accepted:
        verdict = verdicts.get(card.id)
        if (
            verdict is not None
            and verdict.duplicate_of
            and verdict.duplicate_of == card.parent_card_id
        ):
            # a follow-up refines its parent; being close to it is the point, not a repeat
            verdict = verdict.model_copy(update={"duplicate_of": None})
        reasons = audit_reasons(verdict, policy) if verdict else ["the auditor gave no verdict"]
        if reasons:
            rejected.append(RejectedQuestion(card_id=card.id, source="auditor", reasons=reasons))
        else:
            kept.append(card)
    return kept, rejected


def _regroup(original: PlannedBatch, cards: list[Card]) -> list[BatchGroup]:
    titles = {g.id: g.title for g in original.groups}
    order: list[str] = []
    by_group: dict[str, list[Card]] = {}
    for card in cards:
        key = card.group_id or "G1"
        if key not in by_group:
            order.append(key)
            by_group[key] = []
        by_group[key].append(card)
    return [BatchGroup(id=key, title=titles.get(key, ""), cards=by_group[key]) for key in order]


async def _plan_fan_out(
    *,
    planner: QuestionPlanner,
    auditor: QuestionAuditor,
    ctx: TurnContext,
    request: PlanRequest,
    vctx: ValidationContext,
    policy: InterviewPolicy,
    catalog: Catalog,
    statuses: Mapping[str, NeedStatus],
    obligations: list[str],
    needs_table: str,
    asked_list: str,
) -> PipelineResult:
    theme_a_groups = {"scope", "audience", "goals", "product", "features", "idea"}
    needs_a = [
        n
        for n in request.needs
        if getattr(catalog.slots.get(n.need_id), "group", "") in theme_a_groups
    ]
    needs_b = [n for n in request.needs if n not in needs_a]
    if not needs_a or not needs_b:
        mid = max(1, len(request.needs) // 2)
        needs_a = request.needs[:mid]
        needs_b = request.needs[mid:]

    want_max_a = max(2, request.want_max // 2)
    want_max_b = max(2, request.want_max - want_max_a)
    want_min_a = max(1, request.want_min // 2)
    want_min_b = max(1, request.want_min - want_min_a)

    sub_req_a = request.model_copy(
        update={
            "needs": needs_a,
            "focus_needs": [n.need_id for n in needs_a if n.state in ("missing", "partial")],
            "want_min": want_min_a,
            "want_max": want_max_a,
            "next_card_n": request.next_card_n,
            "group_min": 1,
            "group_max": 1,
        }
    )
    sub_req_b = request.model_copy(
        update={
            "needs": needs_b,
            "focus_needs": [n.need_id for n in needs_b if n.state in ("missing", "partial")],
            "want_min": want_min_b,
            "want_max": want_max_b,
            "next_card_n": request.next_card_n + 10,
            "group_min": 1,
            "group_max": 1,
        }
    )

    async def _sub_pipeline(
        sub_req: PlanRequest,
    ) -> tuple[PlannedBatch, list[Card], list[RejectedQuestion], bool]:
        res = PipelineResult(batch=PlannedBatch(batch_id="BAT-000", mode=stored_mode(sub_req.mode)))
        plan_res = await planner.plan(ctx, sub_req)
        if plan_res.used_fallback:
            return plan_res.batch, plan_res.batch.cards(), [], True
        cards, rejected = await _screen(
            cards=plan_res.batch.cards(),
            vctx=vctx,
            auditor=auditor,
            ctx=ctx,
            request=sub_req,
            needs_table=needs_table,
            asked_list=asked_list,
            policy=policy,
            result=res,
        )
        return plan_res.batch, cards, rejected, False

    outcomes = await asyncio.gather(
        _sub_pipeline(sub_req_a),
        _sub_pipeline(sub_req_b),
        return_exceptions=True,
    )

    batches: list[PlannedBatch] = []
    all_cards: list[Card] = []
    all_rejected: list[RejectedQuestion] = []
    any_fallback = False

    for out in outcomes:
        if isinstance(out, BaseException):
            continue
        batch, cards, rejected, used_fb = out
        batches.append(batch)
        all_cards.extend(cards)
        all_rejected.extend(rejected)
        if used_fb:
            any_fallback = True

    if not all_cards:
        fb = await planner.fallback(ctx, request)
        return PipelineResult(
            batch=fb.batch,
            used_fallback=True,
            reason=fb.reason or "fan-out produced no valid cards",
        )

    base_n = request.next_card_n
    renumbered: list[Card] = []
    for i, card in enumerate(all_cards):
        new_id = f"Q-{base_n + i:03d}"
        renumbered.append(card.model_copy(update={"id": new_id}))

    code_merged = validate_batch(rank_by_priority(renumbered), vctx)
    valid_cards = in_original_order(code_merged.accepted, renumbered)
    all_rejected.extend(code_merged.rejected)

    backlog: list[BacklogItem] = []
    for b in batches:
        backlog.extend(b.backlog)

    if len(valid_cards) > request.want_max:
        kept = in_original_order(rank_by_priority(valid_cards)[: request.want_max], valid_cards)
        kept_ids = {c.id for c in kept}
        for extra in (c for c in valid_cards if c.id not in kept_ids):
            backlog.append(
                BacklogItem(
                    topic=extra.intent or extra.question,
                    intent=extra.intent,
                    serves=list(extra.need_ids),
                )
            )
        valid_cards = kept

    missing = request.want_min - len(valid_cards)
    if missing > 0 and request.mode in ASKING_MODES:
        # the validators and the auditor cut the form below its size: one top-up call fills it (live: 3, 3 and 2
        # questions in a row meant extra rounds); its cards are checked together with the ones already accepted
        topup = await plan_validated(
            planner=planner,
            auditor=auditor,
            ctx=ctx,
            request=request.model_copy(
                update={
                    "want_min": missing,
                    "want_max": missing,
                    "next_card_n": base_n + len(all_cards) + 20,
                }
            ),
            vctx=vctx,
            policy=dataclasses.replace(policy, fan_out=False),
            catalog=catalog,
            statuses=statuses,
            obligations=obligations,
            needs_table=needs_table,
            asked_list=asked_list,
        )
        if not topup.used_fallback and topup.batch.cards():
            pool = [*valid_cards, *topup.batch.cards()]
            again = validate_batch(rank_by_priority(pool), vctx)
            valid_cards = in_original_order(again.accepted, pool)[: request.want_max]
            all_rejected.extend(again.rejected)

    merged_hyp = None
    hyps = [b.domain_hypothesis for b in batches if b.domain_hypothesis is not None]
    if hyps:
        merged_hyp = hyps[0]
        if len(hyps) > 1:
            h2 = hyps[1]
            seen_feat = {f.name.casefold() for f in merged_hyp.likely_features}
            merged_features = list(merged_hyp.likely_features) + [
                f for f in h2.likely_features if f.name.casefold() not in seen_feat
            ]
            merged_hyp = merged_hyp.model_copy(
                update={
                    "likely_features": merged_features,
                    "likely_roles": list(dict.fromkeys(merged_hyp.likely_roles + h2.likely_roles)),
                    "likely_families": list(
                        dict.fromkeys(merged_hyp.likely_families + h2.likely_families)
                    ),
                }
            )

    groups = _regroup(
        batches[0] if batches else PlannedBatch(batch_id="BAT-000", mode=stored_mode(request.mode)),
        valid_cards,
    )
    assumed_instead: list[Any] = []
    for b in batches:
        assumed_instead.extend(b.assumed_instead)

    covered_needs = list(dict.fromkeys(c.need_ids[0] for c in valid_cards if c.need_ids))

    merged_batch = PlannedBatch(
        batch_id="BAT-000",
        mode=stored_mode(request.mode),
        groups=groups,
        covered_needs=covered_needs,
        assumed_instead=assumed_instead,
        expert_gaps=[],
        backlog=backlog,
        domain_hypothesis=merged_hyp,
    )
    return PipelineResult(
        batch=merged_batch,
        rejected=all_rejected,
        used_fallback=any_fallback,
        planner_calls=len(batches),
    )


async def plan_validated(
    *,
    planner: QuestionPlanner,
    auditor: QuestionAuditor,
    ctx: TurnContext,
    request: PlanRequest,
    vctx: ValidationContext,
    policy: InterviewPolicy,
    catalog: Catalog,
    statuses: Mapping[str, NeedStatus],
    obligations: list[str],
    needs_table: str,
    asked_list: str,
) -> PipelineResult:
    if (
        policy.fan_out
        and request.mode in ASKING_MODES
        and request.want_max >= 5
        and len(request.needs) >= 2
    ):
        return await _plan_fan_out(
            planner=planner,
            auditor=auditor,
            ctx=ctx,
            request=request,
            vctx=vctx,
            policy=policy,
            catalog=catalog,
            statuses=statuses,
            obligations=obligations,
            needs_table=needs_table,
            asked_list=asked_list,
        )
    result = PipelineResult(batch=PlannedBatch(batch_id="BAT-000", mode=stored_mode(request.mode)))
    first = await planner.plan(ctx, request)
    result.planner_calls = 1
    if first.used_fallback:
        # code-made registry cards: trusted as they are, flagged and counted
        result.batch = first.batch
        result.used_fallback = True
        result.reason = first.reason
        return result
    result.repaired = first.repaired
    cards, rejected = await _screen(
        cards=first.batch.cards(),
        vctx=vctx,
        auditor=auditor,
        ctx=ctx,
        request=request,
        needs_table=needs_table,
        asked_list=asked_list,
        policy=policy,
        result=result,
    )
    assumed = [a.need_id for a in first.batch.assumed_instead]
    uncovered = (
        uncovered_obligations(cards, first.batch.covered_needs, assumed, obligations)
        if request.mode in ASKING_MODES
        else []
    )
    # one repair call for rejected questions and for must/high-impact needs nobody accounted for
    # a repair round costs a plan call and an audit call (about 30 s live): it is only worth it when too few
    # questions survived to fill the batch; gaps the first batch left are planned by the next replan
    short = len(cards) < max(request.want_min, 1) or not policy.repair_only_when_short
    if (
        (rejected or uncovered)
        and short
        and policy.repair_attempts > 0
        and request.mode != "simplify"
    ):
        focus = list(
            dict.fromkeys(
                [
                    *[
                        need
                        for r in rejected
                        for card in first.batch.cards()
                        if card.id == r.card_id
                        for need in card.need_ids
                        if not need.startswith("domain:")
                    ],
                    *uncovered,
                ]
            )
        )
        notes = [f"Repair: {r.card_id} was rejected ({'; '.join(r.reasons)})" for r in rejected[:6]]
        repair_request = request.model_copy(
            update={
                "want_min": 1,
                "want_max": max(1, min(request.want_max, len(rejected) + len(uncovered))),
                "focus_needs": focus or list(request.focus_needs),
                "style_notes": [*request.style_notes, *notes],
                "next_card_n": max(request.next_card_n, _numbers(first.batch.cards()) + 1),
            }
        )
        second = await planner.plan(ctx, repair_request)
        result.planner_calls += 1
        result.repaired = True
        if not second.used_fallback:  # a repair that fell back never replaces a good batch
            keep = replace(
                vctx, planned=(*vctx.planned, *(LedgerEntry.from_card(c) for c in cards))
            )
            more, rejected_again = await _screen(
                cards=second.batch.cards(),
                vctx=keep,
                auditor=auditor,
                ctx=ctx,
                request=repair_request,
                needs_table=needs_table,
                asked_list=asked_list,
                policy=policy,
                result=result,
            )
            cards.extend(more)
            rejected = [*rejected, *rejected_again]
            assumed += [a.need_id for a in second.batch.assumed_instead]
    result.rejected = rejected
    result.uncovered = (
        uncovered_obligations(cards, first.batch.covered_needs, assumed, obligations)
        if request.mode in ASKING_MODES
        else []
    )
    backlog = list(first.batch.backlog)
    if len(cards) > request.want_max:
        kept = in_original_order(rank_by_priority(cards)[: request.want_max], cards)
        kept_ids = {c.id for c in kept}
        for extra in (c for c in cards if c.id not in kept_ids):
            backlog.append(
                BacklogItem(
                    topic=extra.intent or extra.question,
                    intent=extra.intent,
                    serves=list(extra.need_ids),
                )
            )
        cards = kept
    if (
        not cards
        and request.mode in ASKING_MODES
        and any(s.state in ("missing", "partial") for s in statuses.values())
    ):
        fb = await planner.fallback(ctx, request)
        if fb.batch.cards():
            result.batch = fb.batch
            result.used_fallback = True
            result.reason = fb.reason or "every planned question was rejected"
            return result
    result.batch = first.batch.model_copy(
        update={"groups": _regroup(first.batch, cards), "backlog": backlog}
    )
    return result
