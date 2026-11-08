"""The Research engine (spec 4, brief 8.3): a bounded, sequential pipeline, not an agent loop.

    C6 plan -> per query in order: S1 web search -> clean + screen -> C7 extract (untrusted input,
    no tools) -> [R6 internal sources] -> cross-check (code) -> C8 synthesize -> [Deep: gap-fill round
    -> C8 again] -> validate and number (code) -> Research Note

The engine never raises for research failures: it returns a note with ``status`` ``partial`` or
``unavailable`` (``policy | budget | failed | mock_no_fixtures``). Only cancellation propagates
(``CallCancelled``, the flow handles it). LLM access is the ``GatewayClient`` protocol only.
Persistence belongs to the flow.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import InvalidOperation
from typing import Any, cast

from pydantic import BaseModel

from mde_content.packs import load_pack
from mde_content.themes import theme_ids
from mde_contracts.calls import (
    BriefContext,
    C6Input,
    C6Output,
    C7Input,
    C7Output,
    C8Input,
    C8Output,
    ExtractSource,
    FactForSynthesis,
    ResearchPolicyLite,
    S1Input,
)
from mde_contracts.common import Depth
from mde_contracts.engines import ResearchRequest, RunContext
from mde_contracts.gateway import (
    CallContext,
    CallResult,
    CallSpec,
    FreeTextResult,
    GatewayClient,
)
from mde_contracts.ports import InternalFact, InternalSourcePort
from mde_contracts.research import ResearchFact, ResearchJobId, ResearchNote
from mde_core.clock import Clock, SystemClock
from mde_core.errors import BudgetExceeded, CallCancelled
from mde_core.logging import get_logger
from mde_core.money import usd_to_micros
from mde_prompts.calls.c6_plan_research.plan import (
    deterministic_plan,
    queries_per_job,
    templates,
    trim,
    web_jobs,
)
from mde_prompts.registry import load_spec
from mde_research.config import ResearchConfig
from mde_research.context import build_brief_context, build_privacy, scrub_context
from mde_research.crosscheck import (
    JOB_ORDER,
    MergedFact,
    RawFact,
    build_facts,
    max_fact_number,
    merge_raw_facts,
    source_ref,
)
from mde_research.domains import domain_allowed, host_of, safe_http_url, url_key
from mde_research.internal import NullInternalSource
from mde_research.privacy import QueryPrivacy
from mde_research.progress import label_for
from mde_research.safety import (
    PreparedResult,
    check_statement,
    clean_query,
    clean_statement,
    prepare_search_result,
)
from mde_research.synthesis import (
    NoteParts,
    assemble_note,
    build_parts,
    fallback_draft,
    internal_source_ref,
    unavailable_note,
)

log = get_logger("mde_research")

ALL_JOBS: tuple[ResearchJobId, ...] = ("R1", "R2", "R3", "R4", "R5", "R6")
LONG_QUOTE_WORDS = 25
DEFAULT_RUN_CAP_USD = "0.30"
DEFAULT_SESSION_CAP_USD = "0.50"
BLOCKING_TRIPWIRES = frozenset({"must_run_only_80", "refuse_95"})
_WORD = re.compile(r"[a-z0-9]+")


# ----------------------------------------------------------------------------- fallbacks


def s1_skip_query(inp: S1Input) -> FreeTextResult:
    """S1 fallback: this query is skipped (an empty answer, no sources, nothing billed)."""
    return FreeTextResult(text="", citations=[], search_calls=0)


def c6_deterministic_plan(inp: C6Input) -> C6Output:
    """C6 fallback: the deterministic plan derived from the Brief."""
    return deterministic_plan(inp)


def c7_no_facts(inp: C7Input) -> C7Output:
    """C7 fallback: nothing is extracted from this result."""
    return C7Output(facts=[], injection_suspected=False)


def c8_deterministic_suggestions(inp: C8Input) -> C8Output:
    """C8 fallback: suggestions from verified facts only (or none)."""
    return fallback_draft(inp)


FALLBACKS: dict[str, Callable[[Any], object]] = {
    "s1_skip_query": s1_skip_query,
    "c6_deterministic_plan": c6_deterministic_plan,
    "c7_no_facts": c7_no_facts,
    "c8_deterministic_suggestions": c8_deterministic_suggestions,
}


# ----------------------------------------------------------------------------- types


@dataclass(frozen=True)
class ResearchSpecs:
    s1: CallSpec[S1Input, BaseModel]  # output_model is None: free text
    c6: CallSpec[C6Input, C6Output]
    c7: CallSpec[C7Input, C7Output]
    c8: CallSpec[C8Input, C8Output]

    @classmethod
    def load(cls) -> ResearchSpecs:
        return cls(
            s1=load_spec("S1.web_search"),
            c6=load_spec("C6.plan_research"),
            c7=load_spec("C7.extract"),
            c8=load_spec("C8.synthesize_research"),
        )


@dataclass(frozen=True)
class PlannedQuery:
    job: ResearchJobId
    query: str
    gap: bool = False


@dataclass(frozen=True)
class InjectionEvent:
    """A search result or fact that looked like an attempt to instruct the model (spec section 9:
    "noted in the event log"). Reasons are pattern names, never the offending text."""

    job: ResearchJobId
    query_index: int
    stage: str  # screen | model_flag | statement
    reasons: tuple[str, ...]


@dataclass
class ResearchReport:
    """Everything a run did, for the flow's event log and for tests. ``note`` is the result."""

    note: ResearchNote
    queries: list[str] = field(default_factory=lambda: [])
    injection_events: list[InjectionEvent] = field(default_factory=lambda: [])
    dropped: Counter[str] = field(default_factory=lambda: Counter[str]())
    fallbacks_used: list[str] = field(default_factory=lambda: [])
    calls: Counter[str] = field(default_factory=lambda: Counter[str]())
    search_calls: int = 0
    cost_micros: int = 0


@dataclass
class _Outcome:
    """What one search query yielded: validated facts, or why it yielded none."""

    raw: list[RawFact] = field(default_factory=lambda: [])
    error: str | None = None  # budget | failed


@dataclass
class _Run:
    req: ResearchRequest
    ctx: RunContext
    depth: Depth
    raw_ctx: BriefContext
    llm_ctx: BriefContext  # privacy-scrubbed view for the planning and synthesis calls
    privacy: QueryPrivacy
    hint: str
    today: date
    target_jobs: set[ResearchJobId]
    remaining: int = 0
    cost: int = 0
    queries_run: int = 0
    search_calls: int = 0
    done: int = 0
    total: int = 0
    budget_hit: bool = False
    failures: int = 0
    raw: list[RawFact] = field(default_factory=lambda: [])
    executed: list[str] = field(default_factory=lambda: [])
    events: list[InjectionEvent] = field(default_factory=lambda: [])
    dropped: Counter[str] = field(default_factory=lambda: Counter[str]())
    fallbacks: list[str] = field(default_factory=lambda: [])
    calls: Counter[str] = field(default_factory=lambda: Counter[str]())
    web_results: int = 0
    empty_results: int = 0
    empty_modes: set[str] = field(default_factory=lambda: set[str]())
    query_counter: int = 0


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def has_long_quote(statement: str, source_text: str, run: int = LONG_QUOTE_WORDS) -> bool:
    """True when ``statement`` repeats ``run`` or more consecutive words of ``source_text``."""
    words = _words(statement)
    if len(words) < run:
        return False
    text_words = _words(source_text)
    grams = {tuple(text_words[i : i + run]) for i in range(len(text_words) - run + 1)}
    return any(tuple(words[i : i + run]) in grams for i in range(len(words) - run + 1))


def _micros(value: str, default: str) -> int:
    try:
        return usd_to_micros(value)
    except (InvalidOperation, ValueError):
        return usd_to_micros(default)


# ----------------------------------------------------------------------------- the engine


class DefaultResearchEngine:
    """Implements ``mde_contracts.engines.ResearchEngine``."""

    def __init__(
        self,
        gateway: GatewayClient,
        *,
        specs: ResearchSpecs | None = None,
        clock: Clock | None = None,
        internal: InternalSourcePort | None = None,
        config: ResearchConfig | None = None,
    ) -> None:
        self.gateway = gateway
        self.specs = specs or ResearchSpecs.load()
        self.clock: Clock = clock or SystemClock()
        self.internal: InternalSourcePort = internal or NullInternalSource()
        self.config = config or ResearchConfig()
        for spec in (self.specs.s1, self.specs.c6, self.specs.c7, self.specs.c8):
            if spec.fallback is not None and spec.fallback not in FALLBACKS:
                raise ValueError(f"{spec.id}: fallback {spec.fallback!r} is not registered")

    # ------------------------------------------------------------------ public

    async def run(self, req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        return (await self.run_with_report(req, ctx)).note

    async def run_with_report(self, req: ResearchRequest, ctx: RunContext) -> ResearchReport:
        """Like :meth:`run` but also returns what happened (injection events, queries, costs)."""
        brief_hash = req.brief.hash or req.brief.compute_hash()
        now = self.clock.now()
        if not req.policy.research.enabled:
            note = unavailable_note(brief_hash, "policy", req.depth, created_at=now)
            return ResearchReport(note=note)
        if req.rerun_jobs is not None and not req.rerun_jobs and req.previous_note is not None:
            return ResearchReport(note=req.previous_note)
        st = self._start(req, ctx)
        try:
            note = await self._pipeline(st, brief_hash)
        except CallCancelled:
            raise
        except Exception as exc:  # research must never fail the plan: skip it
            log.error("research.failed", error=type(exc).__name__)
            note = unavailable_note(
                brief_hash, "failed", st.depth, queries_run=st.queries_run,
                cost_micros=st.cost, created_at=self.clock.now(),
            )  # fmt: skip
        log.info(
            "research.done",
            status=note.status,
            reason=note.unavailable_reason,
            facts=len(note.facts),
            suggestions=len(note.suggestions),
            queries=st.queries_run,
            cost_micros=st.cost,
            injections=len(st.events),
        )
        return ResearchReport(
            note=note,
            queries=st.executed,
            injection_events=st.events,
            dropped=st.dropped,
            fallbacks_used=st.fallbacks,
            calls=st.calls,
            search_calls=st.search_calls,
            cost_micros=st.cost,
        )

    # ------------------------------------------------------------------ setup

    def _start(self, req: ResearchRequest, ctx: RunContext) -> _Run:
        raw_ctx = build_brief_context(req)
        privacy = build_privacy(req, raw_ctx)
        llm_ctx = scrub_context(raw_ctx, privacy)
        target = set(ALL_JOBS) if req.rerun_jobs is None else set(req.rerun_jobs)
        topic = templates()["topics"].get(raw_ctx.archetype) or raw_ctx.archetype.replace("_", " ")
        hint = f"{topic.strip() or 'general'} app: {llm_ctx.app_summary[:160]}".strip()
        return _Run(
            req=req,
            ctx=ctx,
            depth=req.depth,
            raw_ctx=raw_ctx,
            llm_ctx=llm_ctx,
            privacy=privacy,
            hint=hint,
            today=self.clock.now().date(),
            target_jobs=cast("set[ResearchJobId]", target),
        )

    # ------------------------------------------------------------------ gateway plumbing

    def _call_ctx(self, st: _Run, step: str) -> CallContext:
        ctx = st.ctx
        extra = (ctx.research_scope,) if ctx.research_scope else ()
        return CallContext(
            request_id=ctx.request_id,
            budget_scope=ctx.session_scope,
            extra_scopes=extra,
            purpose=f"research:{step}",
            session_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            cancel=ctx.cancel,
        )

    async def _progress(
        self,
        st: _Run,
        step_key: str,
        label_key: str | None = None,
        *,
        done: int = 0,
        total: int = 0,
    ) -> None:
        step = step_key.split(":", 1)[0]
        label = label_for(label_key or step_key, st.raw_ctx.language)
        try:
            await st.ctx.progress("research", step, label, done, total)
        except CallCancelled:
            raise
        except Exception as exc:  # a broken progress sink must not stop the research
            log.warning("research.progress_failed", error=type(exc).__name__)

    async def _call(
        self, st: _Run, spec: CallSpec[Any, Any], inp: BaseModel, step: str
    ) -> tuple[CallResult[Any] | None, str | None]:
        """One gateway call. Returns ``(result, None)`` or ``(None, "budget" | "failed")``."""
        st.ctx.cancel.check()
        try:
            result: CallResult[Any] = await self.gateway.run(spec, inp, self._call_ctx(st, step))
        except CallCancelled:
            raise
        except BudgetExceeded:
            st.budget_hit = True
            log.warning("research.budget_refused", spec=spec.id, step=step)
            return None, "budget"
        except Exception as exc:  # provider, circuit, schema, cassette ... : research is optional
            st.failures += 1
            log.warning("research.call_failed", spec=spec.id, step=step, error=type(exc).__name__)
            if spec.fallback:
                st.fallbacks.append(spec.fallback)
            return None, "failed"
        st.calls[spec.id] += 1
        st.cost += result.usage.cost_micros
        return result, None

    async def _remaining(self, st: _Run) -> int:
        """Micro-dollars this run may still spend: the tightest of the research cap, the session
        cap and the global effective cap (and zero once a tripwire stops research)."""
        policy = st.req.policy
        cap = _micros(policy.limits.research_budget_usd, DEFAULT_RUN_CAP_USD)
        if self.config.run_cap_micros is not None:
            cap = min(cap, self.config.run_cap_micros)
        spent = st.cost
        limits = [cap]
        try:
            status = await self.gateway.budget_status()
        except Exception as exc:  # unknown budget: rely on our own tally
            log.warning("research.budget_status_failed", error=type(exc).__name__)
            return max(0, cap - spent)
        if status.tripwire in BLOCKING_TRIPWIRES:
            return 0
        scope = st.ctx.research_scope
        if scope:
            spent = max(spent, status.by_scope.get(scope, 0))
        limits[0] = cap - spent
        session_cap = _micros(policy.limits.session_budget_usd, DEFAULT_SESSION_CAP_USD)
        limits.append(session_cap - status.by_scope.get(st.ctx.session_scope, 0))
        limits.append(status.effective_cap_micros - status.spent_micros - status.reserved_micros)
        return max(0, min(limits))

    # ------------------------------------------------------------------ pipeline

    async def _pipeline(self, st: _Run, brief_hash: str) -> ResearchNote:
        cfg = self.config
        req = st.req
        previous = req.previous_note
        merging = previous is not None and req.rerun_jobs is not None
        kept = previous if merging else None  # a rerun that finds nothing keeps the old note
        base_facts: list[ResearchFact] = (
            [f for f in previous.facts if f.job not in st.target_jobs]
            if merging and previous
            else []
        )

        st.ctx.cancel.check()
        st.remaining = await self._remaining(st)
        minimum = cfg.overhead_cost_micros + cfg.per_query_cost_micros
        if st.remaining < minimum:
            return self._fail(st, brief_hash, "budget", kept)
        by_budget = (st.remaining - cfg.overhead_cost_micros) // cfg.per_query_cost_micros
        max_search = max(0, min(cfg.depth_cap(st.depth), by_budget))

        planned = await self._plan(st, max_search)
        if planned is None:
            return self._fail(st, brief_hash, "budget", kept)
        web = [q for q in planned if q.job != "R6"]
        internal = [q for q in planned if q.job == "R6"]
        if not web and not internal:
            return self._fail(st, brief_hash, "failed", kept)

        st.total = len(web)
        await self._search_all(st, web, gap=False)
        await self._internal(st, internal)

        facts = self._facts(st, base_facts, previous if merging else None)
        if len(facts) == len(base_facts):  # nothing new was found
            return self._fail(st, brief_hash, self._empty_reason(st), kept)

        draft = await self._synthesize(st, facts, gap_round=False)
        if st.depth == "deep" and draft.gaps and not st.budget_hit:
            gap_queries = self._gap_queries(st, draft.gaps)
            if gap_queries:
                st.total += len(gap_queries)
                await self._search_all(st, gap_queries, gap=True)
                grown = self._facts(st, base_facts, previous if merging else None)
                if _fact_signature(grown) != _fact_signature(facts):  # something new to weigh
                    facts = grown
                    draft = await self._synthesize(st, facts, gap_round=True)

        st.ctx.cancel.check()  # a run cancelled during the last call is discarded, not stored
        parts = self._parts(st, draft, facts, previous)
        queries_run = st.queries_run + (previous.queries_run if merging and previous else 0)
        cost = st.cost + (previous.cost_micros if merging and previous else 0)
        partial = st.failures > 0 or st.budget_hit
        return assemble_note(
            brief_hash=brief_hash,
            depth=st.depth,
            facts=facts,
            parts=parts,
            status="partial" if partial else "ok",
            reason=("budget" if st.budget_hit else "failed") if partial else None,
            queries_run=queries_run,
            cost_micros=cost,
            created_at=self.clock.now(),
            previous=previous,
        )

    def _fail(
        self, st: _Run, brief_hash: str, reason: str, kept: ResearchNote | None
    ) -> ResearchNote:
        """Nothing usable came out of this run: ``unavailable``, or on a rerun the previous note
        marked ``partial`` (a failed refresh of one job must not erase what we already knew)."""
        if kept is not None and kept.facts and kept.status != "unavailable":
            return kept.model_copy(
                update={
                    "status": "partial",
                    "unavailable_reason": reason,
                    "version": kept.version + 1,
                    "created_at": self.clock.now(),
                }
            ).with_hash()
        return self._unavailable(st, brief_hash, reason)

    def _unavailable(self, st: _Run, brief_hash: str, reason: str) -> ResearchNote:
        return unavailable_note(
            brief_hash,
            reason,
            st.depth,
            queries_run=st.queries_run,
            cost_micros=st.cost,
            created_at=self.clock.now(),
        )

    def _empty_reason(self, st: _Run) -> str:
        if st.budget_hit and st.queries_run == 0:
            return "budget"
        if st.web_results > 0 and st.empty_results == st.web_results and st.empty_modes == {"mock"}:
            return "mock_no_fixtures"
        return "budget" if st.budget_hit and st.failures == 0 else "failed"

    # ------------------------------------------------------------------ planning

    def _jobs_available(self, st: _Run, *, only_targets: bool = True) -> list[ResearchJobId]:
        ctx = st.raw_ctx
        web = st.req.policy.research.web
        names_ok = not st.privacy.restricted
        available: set[ResearchJobId] = {"R1", "R4", "R5"} if ctx.compliance_flags else {"R1", "R5"}
        if names_ok and ctx.references:
            available.add("R2")
        host = host_of(ctx.brand_url) if ctx.brand_url else None
        if names_ok and host and domain_allowed(host, web.allowed_domains, web.blocked_domains):
            available.add("R3")
        if st.req.policy.research.connectors:
            available.add("R6")
        return [j for j in ALL_JOBS if j in available and (not only_targets or j in st.target_jobs)]

    def _clean_one(self, st: _Run, raw: str, seen: set[str]) -> str | None:
        """A planned query as plain generic words: no private data, no forbidden names, no
        duplicate. ``seen`` is updated with the accepted query."""
        cleaned = clean_query(raw, self.config.query_max_chars)
        if cleaned is not None and st.privacy.restricted:
            cleaned = st.privacy.clean_query(cleaned)
        if cleaned is None:
            st.dropped["query_rejected"] += 1
            return None
        key = " ".join(cleaned.casefold().split())
        if key in seen:
            return None
        seen.add(key)
        return cleaned

    def _per_job(self, st: _Run) -> int:
        """How many queries one job may carry in this run: the depth cap spread over the web jobs
        that apply (the one rule in ``plan.queries_per_job``, D-078). It is both the number C6 is
        told (``max_queries_per_job``) and the number the plan is validated against. A rerun of a
        few jobs gets the number its full run had (the jobs the Brief and the policy allow), so a
        refresh does not cost more than the original research did."""
        active = self._jobs_available(st, only_targets=False)
        return queries_per_job(self.config.depth_cap(st.depth), len(web_jobs(active)))

    def _validate_plan(
        self,
        st: _Run,
        plan: C6Output,
        allowed: Sequence[ResearchJobId],
        max_search: int,
        per_job: int,
    ) -> list[PlannedQuery]:
        seen: set[str] = set()
        candidates: dict[ResearchJobId, list[str]] = {}
        for item in plan.jobs:
            if item.job not in allowed:
                st.dropped["job_not_available"] += 1
                continue
            for raw in item.queries:
                if len(candidates.get(item.job, [])) >= per_job:
                    break
                cleaned = self._clean_one(st, raw, seen)
                if cleaned is not None:
                    candidates.setdefault(item.job, []).append(cleaned)
        priority = cast("list[str]", templates()["job_priority"])
        trimmed = trim(candidates, max_search, priority, per_job=per_job)
        return [PlannedQuery(job, q) for job in JOB_ORDER if job in trimmed for q in trimmed[job]]

    async def _plan(self, st: _Run, max_search: int) -> list[PlannedQuery] | None:
        """The queries to run, or ``None`` when the budget refused the planning call."""
        allowed = self._jobs_available(st)
        if not allowed:  # nothing the Brief and the policy justify: no call is made
            return []
        web = st.req.policy.research.web
        per_job = self._per_job(st)
        c6_in = C6Input(
            brief=st.llm_ctx,
            depth=st.depth,
            max_search_calls=max_search,
            max_queries_per_job=per_job,
            remaining_budget_micros=st.remaining,
            policy=ResearchPolicyLite(
                allow_company_name_in_queries=web.allow_company_name_in_queries,
                allowed_domains=list(web.allowed_domains),
                blocked_domains=list(web.blocked_domains),
                connectors=list(st.req.policy.research.connectors),
            ),
            jobs_available=list(allowed),
        )
        await self._progress(st, "plan_research")
        result, error = await self._call(st, self.specs.c6, c6_in, "plan")
        if error == "budget":
            return None
        queries: list[PlannedQuery] = []
        if result is not None and isinstance(result.output, C6Output):
            queries = self._validate_plan(st, result.output, allowed, max_search, per_job)
        elif result is not None:
            self._unusable(st, self.specs.c6)
        if not queries:
            if result is not None and "c6_deterministic_plan" not in st.fallbacks:
                st.fallbacks.append("c6_deterministic_plan")
            queries = self._validate_plan(
                st, c6_deterministic_plan(c6_in), allowed, max_search, per_job
            )
        if "R6" in allowed and not any(q.job == "R6" for q in queries):
            extra = self._validate_plan(st, c6_deterministic_plan(c6_in), ["R6"], 0, per_job)
            queries += [q for q in extra if q.job == "R6"]
        return queries

    def _gap_queries(self, st: _Run, gaps: Sequence[str]) -> list[PlannedQuery]:
        """Gap-fill queries (Deep): C8's open questions as generic, privacy-checked queries."""
        seen = {" ".join(q.casefold().split()) for q in st.executed}
        found: list[PlannedQuery] = []
        for gap in gaps:
            cleaned = self._clean_one(st, gap, seen)
            if cleaned is not None:
                found.append(PlannedQuery("R1", cleaned, gap=True))
        return found[: self.config.gap_fill_max_queries]

    # ------------------------------------------------------------------ search + extract

    async def _search_all(self, st: _Run, queries: Sequence[PlannedQuery], *, gap: bool) -> None:
        sem = asyncio.Semaphore(max(1, self.config.concurrency))

        async def one(index: int, query: PlannedQuery) -> _Outcome:
            async with sem:
                return await self._search_one(st, query, index)

        base = st.query_counter
        st.query_counter += len(queries)
        if self.config.concurrency <= 1:
            outcomes: list[_Outcome | BaseException] = []
            for i, q in enumerate(queries):
                outcomes.append(await one(base + i, q))
        else:
            outcomes = list(
                await asyncio.gather(
                    *(one(base + i, q) for i, q in enumerate(queries)), return_exceptions=True
                )
            )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                if isinstance(outcome, CallCancelled) or not isinstance(outcome, Exception):
                    raise outcome
                st.failures += 1
                continue
            for fact in outcome.raw:
                fact.order = len(st.raw)
                st.raw.append(fact)

    def _unusable(self, st: _Run, spec: CallSpec[Any, Any]) -> None:
        """The call returned, but without the output the spec promises: count it as a failure."""
        st.failures += 1
        if spec.fallback:
            st.fallbacks.append(spec.fallback)

    async def _search_one(self, st: _Run, pq: PlannedQuery, index: int) -> _Outcome:
        cfg = self.config
        st.ctx.cancel.check()
        # a gap-fill search must leave room for the re-synthesis it exists for: without that
        # reserve the second C8 call could be refused and the note would silently fall back
        reserve = cfg.synthesis_cost_micros if pq.gap else 0
        if st.budget_hit or await self._remaining(st) < cfg.per_query_cost_micros + reserve:
            st.budget_hit = True  # also skips the rest of the round: the reserve cannot be kept
            return _Outcome(error="budget")
        web = st.req.policy.research.web
        label = "search:gap" if pq.gap else f"search:{pq.job}"
        await self._progress(st, "search", label, done=st.done, total=st.total)
        allowed = list(web.allowed_domains)
        if pq.job == "R3":
            brand_host = host_of(st.raw_ctx.brand_url or "")
            allowed = [brand_host] if brand_host else allowed
        s1_in = S1Input(query=pq.query, job=pq.job, allowed_domains=allowed, language="en")
        st.executed.append(pq.query)
        result, error = await self._call(
            st, self.specs.s1, s1_in, "gap_search" if pq.gap else "search"
        )
        st.done += 1
        if result is None:
            return _Outcome(error=error)
        st.queries_run += 1
        st.search_calls += max(1, result.search_calls)
        st.web_results += 1
        prepared = prepare_search_result(
            result.text,
            result.citations,
            web,
            max_chars=cfg.max_result_chars,
            max_sources=cfg.max_sources_per_result,
        )
        if prepared.injection:
            self._note_injection(st, pq, index, "screen", prepared.injection)
            return _Outcome()
        if not prepared.sources or not prepared.text.strip():
            st.empty_results += 1
            st.empty_modes.add(result.mode)
            return _Outcome()
        await self._progress(st, "extract", done=st.done, total=st.total)
        return await self._extract(st, pq, index, prepared, gap=pq.gap)

    async def _extract(
        self, st: _Run, pq: PlannedQuery, index: int, prepared: PreparedResult, *, gap: bool
    ) -> _Outcome:
        c7_in = C7Input(
            job=pq.job,
            query=pq.query,
            search_text=prepared.text,
            sources=[ExtractSource(url=s.url, title=s.title) for s in prepared.sources],
            brief_hint=st.hint,
        )
        result, error = await self._call(
            st, self.specs.c7, c7_in, "gap_extract" if gap else "extract"
        )
        if result is None:
            return _Outcome(error=error)
        if not isinstance(result.output, C7Output):
            self._unusable(st, self.specs.c7)
            return _Outcome(error="failed")
        out = result.output
        if out.injection_suspected:
            self._note_injection(st, pq, index, "model_flag", ("model_flag",))
            return _Outcome()
        return self._accept(st, pq, index, out, prepared)

    def _accept(
        self, st: _Run, pq: PlannedQuery, index: int, out: C7Output, prepared: PreparedResult
    ) -> _Outcome:
        cfg = self.config
        by_url = {url_key(s.url): s for s in prepared.sources}
        accepted: list[RawFact] = []
        for fact in out.facts:
            clean = clean_statement(fact.statement, cfg.statement_max_chars)
            reason = check_statement(clean, fact.statement)
            if reason in {"instruction_like", "looks_like_json"}:
                self._note_injection(st, pq, index, "statement", (reason,))
                return _Outcome()
            if reason is not None:
                st.dropped[reason] += 1
                continue
            safe = safe_http_url(fact.source_url)
            source = by_url.get(url_key(safe)) if safe else None
            if source is None:
                st.dropped["unknown_source"] += 1
                continue
            if has_long_quote(clean, prepared.text):
                st.dropped["long_quote"] += 1
                continue
            accepted.append(
                RawFact(
                    order=0,
                    job=pq.job,
                    kind=fact.kind,
                    statement=clean,
                    source=source_ref(source.url, source.title, st.today),
                    impact=fact.impact,
                    confidence=fact.confidence,
                )
            )
            if len(accepted) >= cfg.max_facts_per_result:
                break
        return _Outcome(raw=accepted)

    def _note_injection(
        self, st: _Run, pq: PlannedQuery, index: int, stage: str, reasons: tuple[str, ...]
    ) -> None:
        st.events.append(
            InjectionEvent(job=pq.job, query_index=index, stage=stage, reasons=tuple(reasons))
        )
        st.dropped["injection"] += 1
        log.warning(
            "research.injection_suspected", job=pq.job, query_index=index, stage=stage,
            reasons=list(reasons),
        )  # fmt: skip

    # ------------------------------------------------------------------ internal sources (R6)

    async def _internal(self, st: _Run, queries: Sequence[PlannedQuery]) -> None:
        connectors = list(st.req.policy.research.connectors)
        if not queries or not connectors:
            return
        for pq in queries:
            st.ctx.cancel.check()
            try:
                found: list[InternalFact] = await self.internal.fetch(
                    pq.query, tenant_id=st.ctx.tenant_id, connectors=connectors
                )
            except CallCancelled:
                raise
            except Exception as exc:
                st.failures += 1
                log.warning("research.internal_failed", error=type(exc).__name__)
                continue
            for item in found:
                clean = clean_statement(item.statement, self.config.statement_max_chars)
                reason = check_statement(clean, item.statement)
                if reason is not None:
                    st.dropped[f"internal_{reason}"] += 1
                    continue
                st.raw.append(
                    RawFact(
                        order=len(st.raw),
                        job="R6",
                        kind="other",
                        statement=clean,
                        source=internal_source_ref(item.source, st.today),
                    )
                )

    # ------------------------------------------------------------------ facts, synthesis, note

    def _facts(
        self, st: _Run, base: Sequence[ResearchFact], previous: ResearchNote | None
    ) -> list[ResearchFact]:
        merged: list[MergedFact] = merge_raw_facts(st.raw)
        first = (max_fact_number(previous.facts) + 1) if previous else 1
        first = max(first, max_fact_number(base) + 1)
        room = max(0, self.config.max_facts - len(base))
        new = build_facts(merged, first_number=first, max_facts=room)
        return [*base, *new]

    async def _synthesize(
        self, st: _Run, facts: Sequence[ResearchFact], *, gap_round: bool
    ) -> C8Output:
        c8_in = C8Input(
            brief=st.llm_ctx,
            facts=[
                FactForSynthesis(
                    id=f.id,
                    job=f.job,
                    kind=f.kind,
                    statement=f.statement,
                    source_urls=[s.url for s in f.sources],
                    impact=f.impact,
                    verified=f.verified,
                )
                for f in facts
            ],
            depth=st.depth,
            gap_round=gap_round,
            max_suggestions=self.config.max_suggestions,
        )
        await self._progress(st, "synthesize", done=st.done, total=st.total)
        result, error = await self._call(
            st, self.specs.c8, c8_in, "resynthesize" if gap_round else "synthesize"
        )
        if result is not None and isinstance(result.output, C8Output):
            return result.output
        if result is not None:
            self._unusable(st, self.specs.c8)
        elif error == "budget":
            st.fallbacks.append("c8_deterministic_suggestions")
        return fallback_draft(c8_in)

    def _parts(
        self,
        st: _Run,
        draft: C8Output,
        facts: Sequence[ResearchFact],
        previous: ResearchNote | None,
    ) -> NoteParts:
        return build_parts(
            draft,
            facts=facts,
            ctx=st.llm_ctx,
            cfg=self.config,
            privacy=st.privacy,
            known_features=_pack_features(st.raw_ctx.archetype),
            theme_ids=set(theme_ids()),
            previous=previous,
        )


def _fact_signature(facts: Sequence[ResearchFact]) -> list[tuple[str, tuple[str, ...], bool]]:
    """What a synthesis call can see of the facts: ids, their source urls and whether verified."""
    return [(f.id, tuple(s.url for s in f.sources), f.verified) for f in facts]


def _pack_features(archetype: str) -> set[str]:
    try:
        return {f.id for f in load_pack(archetype).features}
    except KeyError:
        return set()


__all__ = [
    "FALLBACKS",
    "DefaultResearchEngine",
    "InjectionEvent",
    "PlannedQuery",
    "ResearchReport",
    "ResearchSpecs",
    "has_long_quote",
]
