"""The director: plain code that orchestrates a run. No agent loop; the only loop is the bounded round loop.

Steps: intake (done by the caller) -> RSCH-1 plan + deterministic planner + worthiness gate -> round 1 (every
topic, in parallel, no global barrier between topics) -> coverage audit -> gap analyst for unfilled topics ->
round 2 (and 3) only where something is missing, with queries that differ from every earlier one -> checker for
high-impact items -> assemble -> commit. A topic with all required slots filled is ``covered`` and spends nothing
more; after its follow-ups are used it is ``not_found`` (a gap with the queries tried), which is what keeps the loop
from searching forever for something that does not exist (R19). A round that fills fewer than a tenth of the
remaining slots ends the run. Skip stops spending at once and commits what is verified; a topic can be cancelled on
its own; a budget, time or provider stop commits ``partial``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from mde_core.cancel import CancellationToken
from mde_core.errors import BudgetExceeded, CallCancelled, CassetteMissing, LiveCallForbidden
from mde_research_agent.calls import gaps as gap_call
from mde_research_agent.calls import plan as plan_call
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.anatomy import is_repeat
from mde_research_agent.core.slots import Coverage
from mde_research_agent.core.sources import own_site
from mde_research_agent.core.text import tokens
from mde_research_agent.core.urls import registrable_domain
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Plan, Target
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.state import EntityRecord, TargetState
from mde_research_agent.engine.worker import (
    FollowupQuery,
    focus_terms,
    run_topic,
    slot_descriptions,
    update_coverage,
)
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.proposal import PlanProposal, ProposedTarget

REPEATED_FAILURES = 5  # this many failed calls end a run at best partial (RS-34)
TIME_WATCH_S = 0.5


@dataclass
class DirectorResult:
    status: str  # complete | partial | failed | skipped
    reason: str | None = None
    rounds: int = 0
    plan: Plan | None = None
    notes: list[str] = field(default_factory=list)


def proposal_from(out: plan_call.PlanOut) -> PlanProposal:
    return PlanProposal(
        country_code=out.place.country_code,
        country_name=out.place.country_name,
        cities=tuple(out.place.cities),
        category_phrase=out.category_phrase,
        extra_targets=tuple(
            ProposedTarget(
                job=e.job,
                anchor=e.anchor,
                question=e.question,
                decision_unlocked=e.decision_unlocked,
                needs=tuple(e.needs),
                priority=e.priority,
                trivia=e.trivia,
                terms=tuple(e.terms),
            )
            for e in out.extra_targets
        ),
        discovery_names=tuple(out.discovery_names),
    )


def plan_input(ctx: RunContext) -> plan_call.PlanIn:
    return build_plan_in(ctx.input, ctx.catalog)


def build_plan_in(inp: ResearchInput, catalog: Catalog) -> plan_call.PlanIn:
    """The planner call's input for a Brief: the Brief's values plus the topics the deterministic planner already has."""
    base = build_plan(inp, catalog)
    trusted = [r for r in inp.requests if r.origin != "template" and not r.thin]
    return plan_call.PlanIn(
        language=(inp.locales.codes[0] if inp.locales.codes else "en"),
        summary=inp.idea.summary[:600],
        category=inp.idea.category,
        subcategory=inp.idea.subcategory[:200],
        business_model=inp.idea.business_model[:200],
        country=inp.geography.country or "",
        country_code=inp.geography.country_code or "",
        region=inp.geography.region or "",
        cities=list(inp.geography.cities),
        geo_uncertain=inp.geography.uncertain,
        locales=list(inp.locales.codes),
        features=[
            plan_call.FeatureIn(id=f.id, name=f.name, intent=f.intent[:160], priority=f.priority)
            for f in inp.features
            if f.duplicate_of is None
        ][:40],
        roles=[r.name for r in inp.roles if r.duplicate_of is None and not r.is_app_name][:20],
        entities=[e.name for e in inp.entities][:30],
        modules=[m.name for m in inp.modules][:20],
        facts=[
            f.statement[:200]
            for f in inp.facts
            if f.kind in set(catalog.plan.rule_fact_kinds) and not f.is_template
        ][:40],
        requests=[
            plan_call.RequestIn(id=r.id, topic=r.topic[:160], question=r.question[:240])
            for r in trusted
        ][:20],
        existing=[plan_call.ExistingIn(job=t.job, anchor=t.anchors[0]) for t in base.targets],
        declined=list(inp.declined),
    )


def followup_entities(ctx: RunContext, t: Target) -> list[EntityRecord]:
    """The entities a follow-up for ``t`` may be about: those its own topic found, and for a features topic any competitor
    or reference (a provider of messages is no competitor, and a competitor is no payments provider: in the Small run
    the payments topic was offered CoachFitOS and Exotel)."""
    out = []
    for e in ctx.entities.values():
        if (t.id in e.target_ids and e.kind in ("competitor", "provider", "reference")) or (
            t.job == "features" and e.kind in ("competitor", "reference")
        ):
            out.append(e)
    return out


class Director:
    def __init__(self, ctx: RunContext, *, use_models_after_skip: bool = False) -> None:
        self.ctx = ctx
        self.skipped = False
        self._watch: asyncio.Task[None] | None = None
        self.result = DirectorResult(status="complete")
        self.use_models_after_skip = use_models_after_skip

    # --- controls ---------------------------------------------------------------------------------------
    def skip(self) -> None:
        """Stop spending now; the run commits ``skipped`` with what is verified so far."""
        self.skipped = True
        self.ctx.run_cancel.cancel("skip")

    async def cancel_target(self, target_id: str) -> bool:
        ts = self.ctx.targets.get(target_id)
        if ts is None or ts.status in ("covered", "not_found", "cancelled"):
            return False
        ts.cancel.cancel("user_cancelled")
        ts.status = "cancelled"
        ts.reason = "user_cancelled"
        await self.ctx.events.emit(
            "target.cancelled",
            target_id=target_id,
            phase="plan",
            params={"topic": ts.target.label},
            detail={"job": ts.target.job, "topic": ts.target.label},
        )
        return True

    # --- plan -------------------------------------------------------------------------------------------
    async def make_plan(self) -> Plan:
        ctx = self.ctx
        proposal: PlanProposal | None = None
        try:
            with ctx.stage("plan"):
                res = await ctx.runner.run(plan_call.SPEC, plan_input(ctx))
            ctx.budget.add_llm_cost(res.cost_micros, "plan")
            if res.failed:
                ctx.failures.record("llm", res.reason or "failed")
                await ctx.warn("model_failed")
            proposal = proposal_from(res.output)
        except (BudgetExceeded, CassetteMissing, LiveCallForbidden):
            raise
        except CallCancelled:
            proposal = None
        plan = build_plan(ctx.input, ctx.catalog, proposal=proposal)
        ctx.plan = plan
        for t in plan.targets:
            ts = TargetState(target=t, cancel=CancellationToken())
            ts.slots_required = sum(1 for s in t.slots if s.required)
            ctx.targets[t.id] = ts
        labels = ctx.events.labels
        declined = [
            labels.declined(d.label, by_user=d.reason == "declined")
            for d in plan.dropped
            if d.reason in ("declined", "trivia")
        ]
        if plan.light:  # light mode: a small soft ceiling (the hard limits stay as they are)
            lc = ctx.catalog.signals.light
            ctx.budget.soft = ctx.budget.soft.model_copy(
                update={"searches": lc.soft_searches, "seconds": lc.soft_seconds}
            )
        await ctx.events.emit(
            "plan.ready",
            phase="plan",
            params={"n": len(plan.targets)},
            detail={
                "targets": [
                    {
                        "id": t.id,
                        "label": t.label,
                        "job": t.job,
                        "lane": t.lane,
                        "priority": t.priority,
                        "status": "queued",
                        "slots_required": sum(1 for s in t.slots if s.required),
                    }
                    for t in plan.targets
                ],
                "slots_required": sum(1 for t in plan.targets for s in t.slots if s.required),
                "declined": declined,
                "planned_searches": min(
                    ctx.budget.soft.searches,
                    len(plan.targets) * ctx.catalog.ceilings.variants_per_topic,
                ),
                "plan_source": plan.plan_source,
                "country": plan.place.country_code or "",
            },
        )
        return plan

    # --- the rounds --------------------------------------------------------------------------------------
    def _quota(self) -> dict[str, int]:
        """Searches per topic in round 1: three each, fewer for the lowest-ranked topics when the pool is small."""
        ctx = self.ctx
        pool = int(ctx.budget.hard.searches * 0.7)
        per = ctx.catalog.ceilings.variants_per_topic
        ordered = sorted(ctx.targets.values(), key=lambda s: (-s.target.priority, s.target.id))
        quota = {s.target.id: per for s in ordered}
        total = sum(quota.values())
        for level in range(per - 1, 0, -1):
            if total <= pool:
                break
            for s in reversed(ordered):
                if quota[s.target.id] > level:
                    quota[s.target.id] = level
                    total -= 1
                if total <= pool:
                    break
        return quota

    async def _audit(self, round_no: int) -> list[TargetState]:
        """Mark covered topics (they spend no more) and return the topics still missing required details."""
        ctx = self.ctx
        open_: list[TargetState] = []
        for ts in ctx.targets.values():
            if ts.status in ("cancelled", "covered", "not_found"):
                continue
            cov = update_coverage(ts)
            if cov.covered:
                ts.status = "covered"
                await ctx.events.emit(
                    "target.covered",
                    target_id=ts.target.id,
                    round_=round_no,
                    phase="audit",
                    params={"topic": ts.target.label},
                    detail={
                        "job": ts.target.job,
                        "slots_filled": ts.slots_filled,
                        "slots_required": ts.slots_required,
                        "topic": ts.target.label,
                    },
                )
            else:
                open_.append(ts)
        return open_

    def _remaining(self, open_: list[TargetState]) -> int:
        return sum(ts.slots_required - ts.slots_filled for ts in open_)

    def resolve_entity_domains(self) -> int:
        """Give every discovered entity its own domain when a search returned it: the domains the searches returned, most
        often returned first, are matched against EVERY entity of the run with ``own_site`` (not only the entity of the
        topic that asked, which a provider topic never has). Returns how many entities got a domain now."""
        ctx = self.ctx
        counts: dict[str, int] = {}
        for q in ctx.queries:
            for dom in {registrable_domain(u) for u in q.urls}:
                if dom:
                    counts[dom] = counts.get(dom, 0) + 1
        ordered = sorted(counts, key=lambda d: (-counts[d], d))
        found = 0
        for ent in ctx.entities.values():
            if ent.domain:
                continue
            dom = next((d for d in ordered if own_site(ent.name, d)), None)
            if dom:
                ent.domain = dom
                found += 1
        return found

    async def _provider_followups(self) -> None:
        """After round one, for each discovered provider a services topic needs (up to ``provider_followups`` of the load
        class): documentation, pricing and help queries RESTRICTED to the provider's own domain, read as part of the topic
        that found it. Covered topics follow up too: a covered topic only means its slot is filled, not that the provider's
        own pages were read. The searches and pages count against the run's ceilings like any other (``try_search``,
        ``try_page``); the gap analyst's follow-ups after this are unchanged."""
        ctx = self.ctx
        load = ctx.input.load.load_class if ctx.input.load else "standard"
        cap = ctx.catalog.ceilings.for_class(load).provider_followups
        words = ctx.catalog.plan.entity_followup_words
        if cap <= 0 or not words:
            return
        self.resolve_entity_domains()
        services = [
            ts
            for ts in ctx.targets.values()
            if ts.target.job == "services" and ts.status != "cancelled"
        ]

        def owner_of(ent: EntityRecord) -> TargetState | None:
            # the services topic that matters most among those that found the provider (payments before calendar sync)
            mine = [ts for ts in services if ts.target.id in ent.target_ids]
            return (
                max(mine, key=lambda s: (s.target.priority, s.target.id), default=None)
                if mine
                else None
            )

        owners = {
            e.name: owner_of(e) for e in ctx.entities.values() if e.kind == "provider" and e.domain
        }

        def owner_priority(e: EntityRecord) -> int:
            owner = owners.get(e.name)
            return owner.target.priority if owner is not None else 0

        ranked = sorted(
            (e for e in ctx.entities.values() if e.kind == "provider" and e.domain),
            key=lambda e: (
                -owner_priority(e),
                -len(e.target_ids),
                e.name.casefold(),
            ),
        )
        by_topic: dict[str, list[EntityRecord]] = {}
        for ent in ranked[:cap]:
            owner = owners.get(ent.name)
            if owner is not None:
                by_topic.setdefault(owner.target.id, []).append(ent)
        if not by_topic:
            return
        pages = ctx.catalog.ceilings.pages_per_topic.max

        async def one_topic(topic_id: str, ents: list[EntityRecord]) -> None:
            ts = ctx.targets[topic_id]
            for ent in ents:
                if not ctx.active():
                    return
                queries = [
                    FollowupQuery(
                        f"{ent.name} {w}",
                        "en",
                        False,
                        ent.domain,
                        False,
                        "literal",
                    )
                    for w in words
                ]
                await run_topic(ctx, ts, 1, followups=queries, pages=pages, per_domain=pages)
                ctx.count("provider_followups")

        await asyncio.gather(*[one_topic(tid, ents) for tid, ents in by_topic.items()])

    async def _gap_followups(
        self, open_: list[TargetState], round_no: int
    ) -> dict[str, list[FollowupQuery]]:
        ctx = self.ctx
        self.resolve_entity_domains()
        cap = ctx.budget.hard.followups_per_topic
        topics: list[gap_call.GapTopic] = []
        entity_domains: dict[str, dict[int, str | None]] = {}
        for ts in open_:
            if ts.followups >= cap:
                continue
            t = ts.target
            cov: Coverage = update_coverage(ts)
            ents = followup_entities(ctx, t)
            ents = sorted(ents, key=lambda e: (e.domain is None, e.name))[:4]
            entity_domains[t.id] = {i: e.domain for i, e in enumerate(ents)}
            topics.append(
                gap_call.GapTopic(
                    target_id=t.id,
                    job=t.job,
                    anchors=list(t.anchors),
                    context=ctx.plan.category_phrase,
                    place=t.place.text if t.place else "",
                    intent=list(t.intent),
                    missing=slot_descriptions(ts, cov, ctx),
                    missing_terms=focus_terms(ts, cov, ctx),
                    earlier=list(ts.queries),
                    entities=[gap_call.EntityRef(index=i, name=e.name) for i, e in enumerate(ents)],
                )
            )
        if not topics:
            return {}
        with ctx.stage("queries"):
            res = await ctx.runner.run(gap_call.SPEC, gap_call.GapIn(topics=topics))
        ctx.budget.add_llm_cost(res.cost_micros, "queries")
        if res.failed:
            ctx.failures.record("llm", res.reason or "failed")
            await ctx.warn("model_failed")
        out: dict[str, list[FollowupQuery]] = {}
        for f in res.output.followups:
            ts = ctx.targets.get(f.target_id)
            if ts is None:
                continue
            used = [*ts.queries, *(q.text for q in out.get(f.target_id, []))]
            if is_repeat(f.text, used) or len(out.get(f.target_id, [])) >= 2:
                continue
            domain = (
                entity_domains.get(f.target_id, {}).get(f.entity_index)
                if f.entity_index is not None
                else None
            )
            out.setdefault(f.target_id, []).append(
                FollowupQuery(
                    f.text,
                    f.language or "en",
                    f.exact,
                    domain,
                    False,
                    "literal" if domain else "descriptive",
                )
            )
        return out

    watch_interval: float = TIME_WATCH_S

    async def _time_watch(self) -> None:
        """Poll the run clock; at the hard time limit stop starting new work and drain (real sleep, never injected)."""
        ctx = self.ctx
        while True:
            await asyncio.sleep(self.watch_interval)
            if ctx.budget.time_up_hard():
                ctx.drain = True
                ctx.budget_stopped = True
                await ctx.warn("budget_stop")
                return

    async def run_rounds(self) -> None:
        ctx = self.ctx
        budget = ctx.budget
        self._watch = asyncio.create_task(self._time_watch())
        try:
            quota = self._quota()
            ctx.round = 1
            await asyncio.gather(
                *[
                    run_topic(ctx, ts, 1, quota=quota.get(ts.target.id))
                    for ts in ctx.targets.values()
                ]
            )
            if ctx.active() and not ctx.provider_down:
                await self._provider_followups()
            open_ = await self._audit(1)
            open_ = await self._end_no_footprint(open_, 1)
            self._last_gain = self._filled() / max(1, self._required())
            if ctx.verified_count() == 0 and budget.searches_used >= 4 and not ctx.provider_down:
                # nothing came back for ANY topic: a broken provider or locale, not a niche idea. Stop and say so.
                await ctx.warn("no_results")
                ctx.no_results = True
                open_ = []
            await ctx.events.emit(
                "round.completed",
                round_=1,
                phase="audit",
                params={"round": 1, "filled": self._filled(), "required": self._required()},
                detail={
                    "round": 1,
                    "slots_filled": self._filled(),
                    "slots_required": self._required(),
                    "new_slots": self._filled(),
                },
            )
            max_rounds = budget.soft.rounds
            round_no = 1
            while open_ and ctx.active() and round_no < budget.hard.rounds:
                if round_no >= max_rounds and not self._improving():
                    break
                if self._time_stop():
                    break
                if budget.exhausted():
                    ctx.budget_stopped = True
                    break
                if budget.pages_exhausted():
                    # a paid round whose pages cannot be read buys nothing (Small run 2026-11-11: 7 searches, 0 pages)
                    ctx.pages_refused += 1
                    for ts in open_:
                        ts.reason = ts.reason or "budget_stop"
                    await ctx.warn("pages_exhausted")
                    break
                if round_no >= max_rounds:
                    await ctx.warn("taking_longer")
                before = self._remaining(open_)
                filled_before = self._filled()
                plan_followups = await self._gap_followups(open_, round_no + 1)
                runnable = [ts for ts in open_ if plan_followups.get(ts.target.id)]
                for ts in open_:
                    if (
                        not plan_followups.get(ts.target.id)
                        or ts.followups >= budget.hard.followups_per_topic
                    ):
                        await self._not_found(ts, round_no)
                if not runnable:
                    break
                await ctx.events.emit(
                    "gap.detected",
                    round_=round_no + 1,
                    phase="audit",
                    params={"n": len(runnable)},
                    detail={"targets": [ts.target.id for ts in runnable], "count": len(runnable)},
                )
                round_no += 1
                ctx.round = round_no
                for ts in runnable:
                    ts.followups += 1
                await asyncio.gather(
                    *[
                        run_topic(ctx, ts, round_no, followups=plan_followups[ts.target.id])
                        for ts in runnable
                    ]
                )
                open_ = await self._audit(round_no)
                open_ = await self._end_no_footprint(open_, round_no)
                gained = self._filled() - filled_before
                await ctx.events.emit(
                    "round.completed",
                    round_=round_no,
                    phase="audit",
                    params={
                        "round": round_no,
                        "filled": self._filled(),
                        "required": self._required(),
                    },
                    detail={
                        "round": round_no,
                        "slots_filled": self._filled(),
                        "slots_required": self._required(),
                        "new_slots": gained,
                    },
                )
                self._last_gain = gained / before if before else 0.0
                if self._last_gain < ctx.catalog.ceilings.min_round_gain:
                    break
            for ts in open_:
                if ts.status not in ("cancelled", "covered", "not_found"):
                    await self._not_found(ts, round_no)
            self.result.rounds = round_no
        finally:
            if self._watch:
                self._watch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._watch

    _last_gain: float = 1.0

    def _improving(self) -> bool:
        """The last round filled at least ``min_round_gain`` of what was still missing (round one: of all the required
        slots). Past the soft time a run keeps going, up to the hard time, only while this holds."""
        return self._last_gain >= self.ctx.catalog.ceilings.min_round_gain

    def _time_stop(self) -> bool:
        """Past the soft time the run stops unless the last round still paid; then it goes on up to the hard time (the
        time watch drains at that limit). A run that never passed the soft time is not asked."""
        return self.ctx.budget.time_up_soft() and not self._improving()

    async def _end_no_footprint(self, open_: list[TargetState], round_no: int) -> list[TargetState]:
        """A topic about ONE named thing (``plan.named_reference_templates``) that has been searched in two formulations and
        found nothing at all ends ``not_found`` now and leaves the required-slot gate: it is a thing with no public footprint,
        not a run that failed. Topics stopped by a budget, the provider or a skip are not touched (they are ``partial``)."""
        ctx = self.ctx
        named = set(ctx.catalog.plan.named_reference_templates)
        if (
            not named
            or ctx.budget_stopped
            or ctx.drain
            or ctx.provider_down
            or ctx.run_cancel.cancelled
        ):
            return open_
        rest: list[TargetState] = []
        for ts in open_:
            t = ts.target
            if (
                f"{t.job}:{t.template_id}" in named
                and not ts.verified
                and ts.reason != "budget_stop"
                and len({q.casefold() for q in ts.queries}) >= 2
            ):
                ts.status = "not_found"
                ts.reason = "no_public_footprint"
                ts.gate_excluded = True
                update_coverage(ts)
                await ctx.events.emit(
                    "target.not_found",
                    target_id=t.id,
                    round_=round_no,
                    phase="audit",
                    params={"topic": t.label},
                    detail={
                        "job": t.job,
                        "slots_filled": 0,
                        "slots_required": 0,
                        "queries": ts.queries[-6:],
                        "topic": t.label,
                    },
                )
            else:
                rest.append(ts)
        return rest

    def _filled(self) -> int:
        return sum(ts.slots_filled for ts in self.ctx.targets.values())

    def _required(self) -> int:
        return sum(ts.slots_required for ts in self.ctx.targets.values())

    async def _not_found(self, ts: TargetState, round_no: int) -> None:
        """A topic that is still missing details when searching stops: ``not_found`` or ``partial`` with the reason."""
        ctx = self.ctx
        if ts.status in ("cancelled", "covered", "not_found", "partial"):
            return
        stopped = (
            ctx.budget_stopped
            or ctx.drain
            or ctx.provider_down
            or ctx.run_cancel.cancelled
            or ts.reason == "budget_stop"  # it asked for a page and the ceiling was used up
        )
        ts.status = "partial" if stopped else "not_found"
        ts.reason = ts.reason or (
            "budget_stop"
            if ctx.budget_stopped
            else "provider_down"
            if ctx.provider_down
            else "skipped"
            if ctx.run_cancel.cancelled
            else "not_found"
        )
        if ts.status == "not_found":
            await ctx.events.emit(
                "target.not_found",
                target_id=ts.target.id,
                round_=round_no,
                phase="audit",
                params={"topic": ts.target.label},
                detail={
                    "job": ts.target.job,
                    "slots_filled": ts.slots_filled,
                    "slots_required": ts.slots_required,
                    "queries": ts.queries[-6:],
                    "topic": ts.target.label,
                },
            )

    # --- the whole thing ------------------------------------------------------------------------------------
    async def execute(self) -> DirectorResult:
        ctx = self.ctx
        try:
            await self.make_plan()
            await self.run_rounds()
        except (BudgetExceeded, CassetteMissing, LiveCallForbidden) as exc:
            ctx.budget_stopped = ctx.budget_stopped or isinstance(exc, BudgetExceeded)
            self.result.notes.append(f"stopped: {type(exc).__name__}")
            self.result.reason = (
                "budget_stop" if isinstance(exc, BudgetExceeded) else type(exc).__name__
            )
        except CallCancelled:
            pass
        self.result.plan = ctx.plan
        self.result.status, reason = self._final_status()
        self.result.reason = self.result.reason or reason
        return self.result

    def _final_status(self) -> tuple[str, str | None]:
        ctx = self.ctx
        if self.skipped or ctx.run_cancel.cancelled:
            return "skipped", "skipped"
        if ctx.verified_count() == 0 and (
            ctx.provider_down or ctx.no_results or ctx.budget.searches_used == 0
        ):
            return "failed", "provider_down" if ctx.provider_down else "no_results"
        unfinished = [
            ts for ts in ctx.targets.values() if ts.status in ("partial", "queued", "running")
        ]
        if unfinished or ctx.budget_stopped or ctx.provider_down or ctx.drain or ctx.pages_refused:
            reason = (
                "budget_stop"
                if ctx.budget_stopped or ctx.pages_refused
                else "provider_down"
                if ctx.provider_down
                else "unfinished"
            )
            return "partial", reason
        if ctx.failures.total >= REPEATED_FAILURES:
            return "partial", "failures"
        return "complete", None


def label_tokens(text: str) -> set[str]:  # kept for callers that match plan labels
    return set(tokens(text))


def targets_of(ctx: RunContext) -> list[Target]:
    return [ts.target for ts in ctx.targets.values()]


def _unused(_: Any) -> None:  # pragma: no cover
    return None
