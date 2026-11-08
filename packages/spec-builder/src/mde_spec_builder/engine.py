"""The Spec Builder engine (step 4): ``SpecBuilderImpl`` implements ``SpecBuilderEngine``.

    B1 freeze inputs      -> B2 baseline (code) -> B3 C9a/C9b/C9c proposals -> B4 C10 batches
    B5 assemble           -> B6 nine validators -> B7 critic (C12) -> B8 repair (C13, <= 2 rounds)
    B9 build sequence, render, hash, plan summary (C11), Plan view

A valid baseline that the model only refines: proposals are checked by code (``proposals``), a call
that fails is a fallback, never an error (``llm``), and the build ends with ``status="ok"`` (a spec
whose nine validators report no error) or ``status="needs_answer"`` (one plain question, spec 5).

Incremental builds (spec 3.6, ``incremental``): with ``previous_spec`` and ``affected_sections``
only the affected writers re-run and every other block is carried over; toggling a research
addition off removes exactly the items it created.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from mde_content.stack import load_stack_profile
from mde_contracts.calls import (
    C11Input,
    C11Output,
    CriticFinding,
    Proposal,
    SpecProposals,
    ValidatorFinding,
)
from mde_contracts.content import StackProfile
from mde_contracts.engines import BuildRequest, BuildResult, RunContext
from mde_contracts.gateway import CallSpec, GatewayClient
from mde_contracts.research import BrandFinding, ResearchSuggestion
from mde_contracts.spec import AppSpec, AssumptionsBlock, ResearchAddition, RevisionBlock
from mde_core.clock import Clock, SystemClock
from mde_core.ids import new_id
from mde_prompts.registry import SpecNotFound, load_spec
from mde_spec_builder import incremental as inc
from mde_spec_builder.assemble import canonicalize, changed_ids, spec_ids
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.change_ops import Requests, apply_requests
from mde_spec_builder.context import brief_context
from mde_spec_builder.critic import as_validator_findings, run_critic
from mde_spec_builder.llm import LlmRunner
from mde_spec_builder.plan_view import build_plan_view
from mde_spec_builder.proposals import (
    AppliedProposal,
    ApplyResult,
    DroppedProposal,
    validate_and_apply,
)
from mde_spec_builder.render import render_spec, render_unhashed, with_rendered_hash
from mde_spec_builder.repair import derive_question, repair_loop
from mde_spec_builder.research_additions import (
    brand_applies,
    included_suggestions,
    with_affects,
)
from mde_spec_builder.rules import load_rules
from mde_spec_builder.sequence import build_sequence
from mde_spec_builder.summary import (
    clean_lead,
    compose_summary,
    summary_input,
    template_sentence,
)
from mde_spec_builder.validators import run_validators
from mde_spec_builder.writers import (
    MAX_PROPOSALS_PER_BATCH,
    MAX_PROPOSALS_PER_CALL,
    SectionSpecs,
    run_screen_briefs,
    run_section_writers,
)
from mde_spec_loader.tokens import estimate_tokens

log = logging.getLogger(__name__)

CALL_IDS = {
    "c9a": "C9a.screens_nav",
    "c9b": "C9b.data_api_roles",
    "c9c": "C9c.design_tokens",
    "c10": "C10.screen_briefs",
    "c11": "C11.write_summary",
    "c12": "C12.critic",
    "c13": "C13.repair",
}
SummaryLoader = Callable[[], CallSpec[C11Input, C11Output]]
TOTAL_STEPS = 10


@dataclass(frozen=True)
class BuilderSettings:
    """Knobs of the build driver (all have safe defaults)."""

    concurrency: int = 1  # calls in flight at once (1 = strictly sequential)
    c10_batch_size: int = 4  # screens per C10 call (3 to 4)
    c10_retries: int = 1  # extra attempts of a C10 batch before its fallback
    c10_batch_limit: int | None = None  # at most this many C10 batches (None = all screens)
    repair_rounds: int = 2  # at most two (spec 5)
    critic: bool = True
    summary_words: int = 60
    strict_proposals: bool = False  # run all nine validators after every proposal (slow)


@dataclass
class _Run:
    """Mutable state of one build."""

    req: BuildRequest
    ctx: RunContext
    runner: LlmRunner
    stack: StackProfile
    applied: list[AppliedProposal] = field(default_factory=list)
    dropped: list[DroppedProposal] = field(default_factory=list)
    touched: dict[str, list[str]] = field(default_factory=dict)  # research ref -> ids it touched
    tokens: int = 0
    step: int = 0
    realized: set[str] = field(default_factory=set)  # additions code already applied
    lines: list[str] = field(default_factory=list)  # what a request could not do, in plain words


class SpecBuilderImpl:
    """``SpecBuilderEngine`` over a ``GatewayClient`` (in-process or HTTP)."""

    def __init__(
        self,
        gateway: GatewayClient,
        *,
        clock: Clock | None = None,
        summary_loader: SummaryLoader | None = None,
        settings: BuilderSettings | None = None,
        specs: dict[str, CallSpec[Any, Any]] | None = None,
        new_id_fn: Callable[[str], str] = new_id,
    ) -> None:
        self._gateway = gateway
        self._clock = clock or SystemClock()
        self._settings = settings or BuilderSettings()
        self._new_id = new_id_fn
        self._summary_loader: SummaryLoader = summary_loader or (lambda: load_spec(CALL_IDS["c11"]))
        self._specs = specs if specs is not None else {}
        self._rules = load_rules()

    # ------------------------------------------------------------------ public

    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult:
        run = _Run(
            req=req,
            ctx=ctx,
            runner=LlmRunner(self._gateway, ctx),
            stack=load_stack_profile(),
        )
        await self._progress(run, "freeze", "Reading your approved brief")  # B1
        plan = inc.plan_build(req)
        base = build_baseline(req)  # B2
        run.realized = {a.id for a in base.assumptions.research_additions if a.affects}
        prev = req.previous_spec
        if plan.mode == "patch" and prev is not None and inc.structure_matches(prev, base):
            spec, keep_summary, light = await self._patch(run, plan, prev, base)
        else:
            await self._progress(run, "baseline", "Laying out the screens and data")
            # what the person asked about screens (and in the earlier version) goes in before the
            # writers run, so every one of them works on the screens as they will be
            spec = self._requests(run, base, previous=prev).spec
            spec = await self._refine_full(run, spec)
            keep_summary, light = False, False
        spec = self._finalize(run, spec)  # B5
        return await self._check_and_finish(run, spec, keep_summary=keep_summary, light=light)

    # ------------------------------------------------------------------ helpers

    def _call_spec(self, key: str) -> CallSpec[Any, Any] | None:
        call_id = CALL_IDS[key]
        if call_id not in self._specs:
            try:
                self._specs[call_id] = load_spec(call_id)
            except SpecNotFound:
                log.warning(
                    "builder: call spec missing, using its fallback", extra={"call": call_id}
                )
                return None
        return self._specs[call_id]

    async def _progress(
        self, run: _Run, step: str, label: str, done: int | None = None, total: int | None = None
    ) -> None:
        run.ctx.cancel.check()
        if done is None:
            run.step += 1
        await run.ctx.progress(
            "build",
            step,
            label,
            run.step if done is None else done,
            TOTAL_STEPS if total is None else total,
        )

    def _section_specs(self) -> SectionSpecs:
        return SectionSpecs(
            c9a=self._call_spec("c9a"),
            c9b=self._call_spec("c9b"),
            c9c=self._call_spec("c9c"),
            c10=self._call_spec("c10"),
        )

    def _apply(
        self, run: _Run, spec: AppSpec, proposals: SpecProposals | None, cap: int
    ) -> AppSpec:
        """Apply one call's proposals (the first ``cap`` of them); count and remember the rest."""
        if proposals is None:
            return spec
        items = list(proposals.proposals)
        result = self._validate_and_apply(run, spec, items[:cap])
        for extra in items[cap:]:
            result.dropped.append(
                DroppedProposal(
                    proposal=extra, code="limit", reason="too many proposals in one answer"
                )
            )
        self._record(run, result)
        return result.spec

    def _validate_and_apply(
        self, run: _Run, spec: AppSpec, items: Sequence[Proposal]
    ) -> ApplyResult:
        limits = run.req.policy.limits
        return validate_and_apply(
            spec,
            items,
            research=run.req.research,
            excluded=run.req.excluded_research_ids,
            limits=limits,
            stack_profile=run.stack,
            max_tokens=limits.max_spec_tokens,
            tokens_used=run.tokens,
            strict=self._settings.strict_proposals,
        )

    def _record(self, run: _Run, result: ApplyResult) -> None:
        run.applied += result.applied
        run.dropped += result.dropped
        for item in result.applied:
            run.tokens += estimate_tokens(item.proposal.model_dump_json())
            ref = item.proposal.research_ref
            if item.proposal.source == "research" and ref:
                run.touched.setdefault(ref, []).extend(item.touched_ids)

    def _requests(self, run: _Run, spec: AppSpec, *, previous: AppSpec | None = None) -> Requests:
        """Screen notes and screen-level change requests, through the same checks as proposals."""
        req = run.req
        out = apply_requests(
            spec,
            req.change_ops,
            req.screen_notes,
            lambda current, items: self._validate_and_apply(run, current, items),
            previous=previous,
        )
        run.applied += out.applied
        run.dropped += out.dropped
        run.lines += out.lines
        return out

    def _research(self, run: _Run) -> list[ResearchSuggestion]:
        """Included suggestions the writers still have to deal with: the ones the baseline already
        applied (a pack feature switched on from ``applies_to``) are not asked of the model."""
        found = included_suggestions(run.req.research, run.req.excluded_research_ids)
        return [s for s in found if s.id not in run.realized]

    def _brand(self, run: _Run) -> BrandFinding | None:
        req = run.req
        if req.research is not None and brand_applies(req.research, req.excluded_research_ids):
            return req.research.brand
        return None

    # ------------------------------------------------------------------ B3 / B4

    async def _refine_full(self, run: _Run, spec: AppSpec) -> AppSpec:
        """Baseline -> section writers -> screen briefs (everything)."""
        run.tokens = estimate_tokens(render_unhashed(spec))
        return await self._refine(
            run, spec, writers=("C9a", "C9b", "C9c"), c10_only=None, research=self._research(run)
        )

    async def _refine(
        self,
        run: _Run,
        spec: AppSpec,
        *,
        writers: Sequence[str],
        c10_only: set[str] | None,
        research: Sequence[ResearchSuggestion],
        c10_research: Sequence[ResearchSuggestion] | None = None,
    ) -> AppSpec:
        brief = brief_context(run.req, spec)
        specs = self._section_specs()
        settings = self._settings
        if writers:
            await self._progress(run, "screens", "Drafting the screens, data and look")
            written = await run_section_writers(
                run.runner,
                specs,
                spec,
                brief,
                research,
                self._brand(run),
                which=writers,
                max_screens=run.req.policy.limits.max_screens,
                concurrency=settings.concurrency,
            )
            for _name, proposals in written:
                spec = self._apply(run, spec, proposals, MAX_PROPOSALS_PER_CALL)
        if c10_only is None or c10_only:
            await self._progress(run, "briefs", "Writing each screen")
            brief = brief_context(run.req, spec)

            async def on_batch(done: int, total: int) -> None:
                await self._progress(run, "briefs", "Writing each screen", done, total)

            results = await run_screen_briefs(
                run.runner,
                specs.c10,
                spec,
                brief,
                research if c10_research is None else c10_research,
                only=c10_only,
                batch_size=settings.c10_batch_size,
                retries=settings.c10_retries,
                concurrency=settings.concurrency,
                limit=settings.c10_batch_limit,
                on_batch=on_batch,
            )
            for proposals in results:
                spec = self._apply(run, spec, proposals, MAX_PROPOSALS_PER_BATCH)
        return spec

    # ------------------------------------------------------------------ incremental

    async def _patch(
        self, run: _Run, plan: inc.BuildPlan, prev: AppSpec, base: AppSpec
    ) -> tuple[AppSpec, bool, bool]:
        """Re-run only the affected writers on ``prev``. Returns (spec, keep the old summary,
        a light change that needs no critic)."""
        await self._progress(run, "baseline", "Applying your change")
        tog = inc.toggles(prev, run.req.excluded_research_ids)
        sections = set(plan.sections)
        if any(a.kind == "brand" for a in [*tog.off, *tog.on]):
            sections.add("design")  # the brand finding of the Note drives the look
        spec = prev
        writers: list[str] = []
        c10_ids: set[str] = set()
        language_changed = prev.header.language != base.header.language
        if "design" in sections:
            spec = inc.graft_design(spec, base)
            writers.append("C9c")
        if "roles" in sections:
            spec, replaced = inc.graft_roles(spec, base)
            writers.append("C9b")
            c10_ids |= replaced
        if "integrations" in sections:
            before = spec
            spec = inc.graft_integrations(spec, base)
            c10_ids |= inc.affected_screens(spec, inc.changed_integration_ids(before, spec))
        if "behavior" in sections:
            spec = inc.graft_behavior(spec, base)
            spec = spec.model_copy(
                update={"app": spec.app.model_copy(update={"language": base.app.language})}
            )
            if language_changed:
                c10_ids |= {s.id for s in spec.screens}
        spec = self._remove_toggled(run, spec, tog.off)
        requested = self._requests(run, spec)  # screen notes and change requests of this build
        spec = requested.spec
        c10_ids |= requested.briefs
        # the file always follows the current inputs: header, assumptions, acceptance
        spec = spec.model_copy(
            update={
                "header": base.header,
                "revision": base.revision,
                "assumptions": self._merge_assumptions(prev, base.assumptions, tog.off),
                "acceptance": base.acceptance,
            }
        )
        run.tokens = estimate_tokens(render_unhashed(spec))
        run.realized = {a.id for a in spec.assumptions.research_additions if a.affects}
        research = self._research(run)
        added = [s for s in research if s.id in {a.id for a in tog.on}]
        if added:  # switched back on: the writers see only these additions
            writers = sorted({*writers, "C9a", "C9b"})
        spec = await self._refine(
            run,
            spec,
            writers=writers,
            c10_only=c10_ids,
            research=added if added else (research if writers else []),
            c10_research=research,
        )
        quiet = not tog.on and not tog.off and not requested.changed
        light = set(sections) <= {"design", "behavior", "integrations"} and quiet
        keep_summary = set(sections) <= {"design"} and quiet
        return spec, keep_summary, light

    def _remove_toggled(self, run: _Run, spec: AppSpec, off: Sequence[ResearchAddition]) -> AppSpec:
        """Switch research additions off: remove exactly the items they created."""
        for addition in off:
            ids = inc.created_by(spec, addition)
            items = [
                Proposal(
                    kind="remove_item",
                    target=i,
                    source="repair",
                    rationale="Switched off at Gate 2",
                )
                for i in ids
            ]
            result = self._validate_and_apply(run, spec, items)
            run.dropped += result.dropped  # a removal that is not allowed is logged, never silent
            run.applied += result.applied
            spec = result.spec
        return spec

    def _merge_assumptions(
        self,
        prev: AppSpec,
        fresh: AssumptionsBlock,
        off: Sequence[ResearchAddition],
    ) -> AssumptionsBlock:
        """The fresh assumptions with each research addition's ``affects`` carried over."""
        carried = {a.id: a.affects for a in prev.assumptions.research_additions}
        gone = {a.id for a in off}
        additions = [
            a.model_copy(update={"affects": [] if a.id in gone else carried.get(a.id, [])})
            for a in fresh.research_additions
        ]
        return fresh.model_copy(update={"research_additions": additions})

    # ------------------------------------------------------------------ B5

    def _finalize(self, run: _Run, spec: AppSpec) -> AppSpec:
        spec = canonicalize(spec)
        spec = with_affects(spec, run.touched)
        return spec.model_copy(update={"build": build_sequence(spec)})

    def _validate(self, run: _Run, spec: AppSpec) -> list[ValidatorFinding]:
        limits = run.req.policy.limits
        return run_validators(
            spec, stack_profile=run.stack, limits=limits, max_tokens=limits.max_spec_tokens
        )

    # ------------------------------------------------------------------ B6 - B9

    async def _check_and_finish(
        self, run: _Run, spec: AppSpec, *, keep_summary: bool, light: bool
    ) -> BuildResult:
        req = run.req
        await self._progress(run, "validate", "Checking everything fits together")  # B6
        findings = self._validate(run, spec)
        brief = brief_context(req, spec)
        critic_spec = self._call_spec("c12") if self._settings.critic and not light else None
        critic: list[CriticFinding] = []
        if critic_spec is not None:
            await self._progress(run, "critic", "Checking it matches what you asked")  # B7
            critic = await run_critic(run.runner, critic_spec, spec, brief, findings)
        rounds = 0
        errors_exist = any(f.severity == "error" for f in findings) or any(
            f.severity == "error" for f in critic
        )
        if errors_exist:
            await self._progress(run, "repair", "Fixing small problems")  # B8
            outcome = await repair_loop(
                spec,
                brief,
                findings,
                critic,
                runner=run.runner,
                repair_spec=self._call_spec("c13"),
                critic_spec=critic_spec,
                validate=lambda s: self._validate(run, s),
                apply=lambda s, items: self._validate_and_apply(run, s, items),
                max_rounds=self._settings.repair_rounds,
            )
            run.applied += outcome.applied
            run.dropped += outcome.dropped
            for item in outcome.applied:
                ref = item.proposal.research_ref
                if item.proposal.source == "research" and ref:
                    run.touched.setdefault(ref, []).extend(item.touched_ids)
            spec = self._finalize(run, outcome.spec)
            findings, critic, rounds = outcome.validators, outcome.critic, outcome.rounds
            errors = outcome.errors
            if errors:
                return self._needs_answer(run, spec, findings, critic, rounds, errors)
        return await self._finish(run, spec, findings, critic, rounds, keep_summary)

    def _needs_answer(
        self,
        run: _Run,
        spec: AppSpec,
        findings: list[ValidatorFinding],
        critic: list[CriticFinding],
        rounds: int,
        errors: Sequence[ValidatorFinding | CriticFinding],
    ) -> BuildResult:
        card, slot = derive_question(
            errors, run.req, self._rules, clock=self._clock, new_id=self._new_id
        )
        return BuildResult(
            status="needs_answer",
            spec=spec,
            question=card,
            question_slot=slot,
            findings=[*findings, *as_validator_findings(critic)],
            proposals_applied=len(run.applied),
            proposals_dropped=len(run.dropped),
            repair_rounds=rounds,
        )

    async def _plan_summary(self, run: _Run, spec: AppSpec) -> str:
        """The lead sentence (C11 ``plan_summary``), or the template sentence."""
        try:
            call_spec = self._summary_loader()
        except SpecNotFound:
            return template_sentence(spec, self._rules)
        out = await run.runner.call(
            call_spec, summary_input(spec, self._settings.summary_words), step="summary"
        )
        return clean_lead(out.text if out is not None else None) or template_sentence(
            spec, self._rules
        )

    async def _finish(
        self,
        run: _Run,
        spec: AppSpec,
        findings: list[ValidatorFinding],
        critic: list[CriticFinding],
        rounds: int,
        keep_summary: bool,
    ) -> BuildResult:
        req = run.req
        prev = req.previous_spec
        await self._progress(run, "finish", "Putting the plan together")  # B9
        spec = spec.model_copy(update={"build": build_sequence(spec)})
        if keep_summary and prev is not None:
            lead = prev.summary.split("\n\n")[0]
            summary = prev.summary
        else:
            lead = await self._plan_summary(run, spec)
            summary = compose_summary(spec, lead, self._rules)
        revision = RevisionBlock(
            version=req.spec_version,
            previous_version=prev.header.spec_version if prev is not None else None,
            changed_ids=changed_ids(prev, spec.model_copy(update={"summary": summary}))
            if prev is not None
            else [],
        )
        spec = with_rendered_hash(
            spec.model_copy(update={"summary": summary, "revision": revision})
        )
        text = render_spec(spec)
        gate1_ids = list(req.gate1_spec_ids) or (
            spec_ids(req.gate1_spec) if req.gate1_spec is not None else []
        )
        plan = build_plan_view(
            spec,
            summary=lead,
            brief=req.brief,
            research_note=req.research,
            policy=req.policy,
            gate1_ids=gate1_ids,
            gate1_spec=req.gate1_spec,
            notes=run.lines,
        )
        return BuildResult(
            status="ok",
            spec=spec,
            spec_text=text,
            spec_hash=spec.header.hash,
            plan=plan,
            findings=[*findings, *as_validator_findings(critic)],
            proposals_applied=len(run.applied),
            proposals_dropped=len(run.dropped),
            repair_rounds=rounds,
        )


__all__ = ["BuilderSettings", "SpecBuilderImpl"]
