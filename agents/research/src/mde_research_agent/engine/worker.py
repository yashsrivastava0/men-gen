"""The per-topic worker: a FIXED pipeline (not an agent) that finds, reads, extracts and verifies for ONE topic.

1. write 2 or 3 query variants (model, with a deterministic fallback), each checked by the anatomy gate;
2. search them in parallel through the SearchPort (country, language, recency, exact match), fuse by reciprocal
   rank, dedupe by canonical URL;
3. triage by code (source class, snippet relevance, one or two pages per domain) to the best 3 to 5 pages;
4. read each page (provider text, else FetchPort), select passages around the slot terms;
5. extract typed items (model, no tools), then VERIFY by code: the quote must be on the stored page (one retry
   "quote exactly", else dropped), strings screened, lengths and payload checked.

A topic returns only verified items. Failures never raise out of here: they are counted, shown as warnings and the
topic ends with what it has. Cancellation (the run's or the topic's) stops further stages; a budget stop ends the
run. Pages are shared between topics through ``ctx.pages`` so a page is fetched once but extracted per job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mde_core.errors import BudgetExceeded, CallCancelled, CassetteMissing, LiveCallForbidden
from mde_research_agent.calls import queries as q_calls
from mde_research_agent.calls.extract import (
    JOB_KINDS,
    KIND_GUIDE,
    ExtractIn,
)
from mde_research_agent.calls.extract import (
    SPECS as EXTRACT_SPECS,
)
from mde_research_agent.core.anatomy import check_query, is_repeat
from mde_research_agent.core.hashing import text_hash
from mde_research_agent.core.language import guess_language
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.passages import bm25_scores, render, select_passages
from mde_research_agent.core.rrf import FusedHit, fuse
from mde_research_agent.core.slots import Coverage, evaluate_target
from mde_research_agent.core.sources import classify_source, own_site
from mde_research_agent.core.text import stem, tokens
from mde_research_agent.core.urls import canonical_url, is_public_url, registrable_domain
from mde_research_agent.domain.plan import Target
from mde_research_agent.engine.context import (
    FETCH_TIMEOUT_S,
    PASSAGE_CHARS,
    RATE_LIMIT_WAIT_CAP_S,
    SEARCH_TIMEOUT_S,
    SEARCH_TRANSPORT_ATTEMPTS,
    RunContext,
)
from mde_research_agent.engine.state import (
    AtomRecord,
    EntityRecord,
    PageRecord,
    QueryRecord,
    TargetState,
)
from mde_research_agent.engine.verify import (
    Candidate,
    Rejected,
    candidates_from_output,
    check_candidate,
)
from mde_research_agent.ports.search import SearchError, SearchRequest, SearchResult

STOP_RUN: tuple[type[BaseException], ...] = (BudgetExceeded, CassetteMissing, LiveCallForbidden)
CLASS_WEIGHT = {
    "official": 1.0,
    "help": 0.8,
    "press": 0.6,
    "store": 0.5,
    "blog": 0.3,
    "forum": 0.15,
    "seo": 0.1,
}
MIN_PROVIDER_TEXT = 300


@dataclass(frozen=True)
class FollowupQuery:
    text: str
    language: str = "en"
    exact: bool = False
    domain: str | None = None  # an entity's own domain: restrict the search to it
    seed: bool = False
    variant: str = "descriptive"


# --- coverage helpers -----------------------------------------------------------------------------------


def update_coverage(ts: TargetState) -> Coverage:
    cov = evaluate_target(ts.target.slots, ts.verified)
    ts.slots_required = cov.required_total
    ts.slots_filled = cov.required_filled
    return cov


def slot_descriptions(ts: TargetState, cov: Coverage, ctx: RunContext) -> list[str]:
    out = []
    for slot, fill in zip(ts.target.slots, cov.fills, strict=True):
        if fill.filled or not slot.required:
            continue
        rich = f", each with {slot.attr_min}+ {slot.attr}" if slot.attr and slot.attr_min else ""
        what = (
            f"{slot.min} distinct {slot.kind} items{rich}"
            if slot.type == "count"
            else f"{slot.min} distinct {slot.attr} values"
        )
        out.append(f"{slot.key}: need {what} (found {fill.count})")
    return out


def unfilled_kinds(ts: TargetState, cov: Coverage) -> list[str]:
    kinds = [
        s.kind
        for s, f in zip(ts.target.slots, cov.fills, strict=True)
        if s.required and not f.filled
    ]
    return list(dict.fromkeys(kinds or [s.kind for s in ts.target.slots]))


def focus_terms(ts: TargetState, cov: Coverage, ctx: RunContext) -> list[str]:
    words: list[str] = []
    for kind in unfilled_kinds(ts, cov):
        words += list(ctx.catalog.lanes.kinds[kind].terms)
    for s, f in zip(ts.target.slots, cov.fills, strict=True):
        if s.required and not f.filled and s.attr:
            words.append(s.attr)
    return list(dict.fromkeys(words))[:8]


# --- queries -------------------------------------------------------------------------------------------


def _intent_words(ctx: RunContext) -> list[str]:
    return [w for lang in ctx.catalog.intent_words.languages.values() for w in lang.words]


def _check(
    ctx: RunContext,
    t: Target,
    text: str,
    *,
    extra_anchors: Sequence[str] = (),
    require_place: bool | None = None,
) -> bool:
    intent = [*_intent_words(ctx), *t.intent]
    res = check_query(
        text,
        anchors=[*t.anchors, *extra_anchors],
        places=list(t.place.phrases) if t.place else [],
        intent_words=intent,
        generic_words=ctx.catalog.intent_words.generic_words,
        own_brand=ctx.input.own_brand_terms,
        require_place=bool(t.place and not t.place.unknown)
        if require_place is None
        else require_place,
    )
    return res.ok


def build_query_in(
    ctx: RunContext, ts: TargetState, round_no: int, cov: Coverage, want: int
) -> q_calls.QueryIn:
    t = ts.target
    langs = list(t.place.languages[:1]) if t.place else []
    local_terms = [s for s in ctx.input.hints.synonyms if not s.isascii()][:3] if langs else []
    info = ctx.catalog.intent_words.languages.get(langs[0]) if langs else None
    seeds = (
        list(ctx.plan.discovery_names[:2])
        if round_no == 1 and t.template_id in {"landscape", "providers"}
        else []
    )
    return q_calls.QueryIn(
        job=t.job,
        question=t.question,
        anchors=list(t.anchors),
        context=ctx.plan.category_phrase,
        place=t.place.text if t.place else "",
        languages=langs,
        local_terms=local_terms,
        local_intent=list(info.words[:3]) if info else [],
        intent=list(t.intent),
        entity=t.entity,
        earlier=list(ts.queries),
        want=want,
        round=round_no,
        focus_terms=focus_terms(ts, cov, ctx) if round_no > 1 else [],
        seed_names=seeds,
    )


async def write_variants(
    ctx: RunContext, ts: TargetState, round_no: int, cov: Coverage, want: int
) -> list[FollowupQuery]:
    """Variants from the model, each checked by the anatomy gate; the deterministic builder when none survives."""
    t = ts.target
    inp = build_query_in(ctx, ts, round_no, cov, want)
    valid: list[FollowupQuery] = []
    async with ctx.llm_sem:
        res = await ctx.runner.run(q_calls.SPEC, inp)
    ctx.budget.add_llm_cost(res.cost_micros)
    if res.failed:
        ctx.failures.record("llm", res.reason or "failed", t.id)
        await ctx.warn("model_failed", target_id=t.id)
    for v in res.output.variants:
        extra = [*inp.local_terms] if v.style == "local" else [*inp.seed_names] if v.seed else []
        if not is_repeat(v.text, [*ts.queries, *(x.text for x in valid)]) and _check(
            ctx, t, v.text, extra_anchors=extra
        ):
            valid.append(FollowupQuery(v.text, v.language, v.exact, None, v.seed, v.style))
    if not valid:
        for v in q_calls.deterministic_variants(inp).variants:
            extra = (
                [*inp.local_terms] if v.style == "local" else [*inp.seed_names] if v.seed else []
            )
            if not is_repeat(v.text, [*ts.queries, *(x.text for x in valid)]) and _check(
                ctx, t, v.text, extra_anchors=extra
            ):
                valid.append(FollowupQuery(v.text, v.language, v.exact, None, v.seed, v.style))
    return valid[: want + 1]


# --- search --------------------------------------------------------------------------------------------


async def _search_call(ctx: RunContext, req: SearchRequest) -> SearchResult:
    try:
        return await asyncio.wait_for(ctx.search.search(req), timeout=SEARCH_TIMEOUT_S)
    except TimeoutError as exc:
        raise SearchError("timeout", "search timed out") from exc


async def search_one(
    ctx: RunContext, ts: TargetState, fq: FollowupQuery, round_no: int
) -> SearchResult | None:
    t = ts.target
    if not ctx.active() or ts.cancel.cancelled or not ctx.budget.try_search():
        if ctx.budget.exhausted():
            ctx.budget_stopped = True
            ts.reason = ts.reason or "budget_stop"
            await ctx.warn("budget_stop")
        return None
    place = t.place
    req = SearchRequest(
        query=fq.text,
        country=place.country_code if place else None,
        language=fq.language,
        domain_mode="restrict" if fq.domain else "none",
        domains=(fq.domain,) if fq.domain else (),
        exact=fq.exact,
        recency_months=t.recency_months,
        want_content=bool(getattr(ctx.search.capabilities, "page_content", False)),
        max_results=10,
    )
    ts.queries.append(fq.text)
    await ctx.events.emit(
        "query.issued",
        target_id=t.id,
        round_=round_no,
        phase="search",
        params={"query": fq.text},
        detail={
            "query": fq.text,
            "variant": fq.variant,
            "lang": fq.language,
            "country": req.country or "",
            "provider": ctx.search.name,
            "domain": fq.domain or "",
        },
    )
    result: SearchResult | None = None
    async with ctx.search_sem:
        for attempt in range(SEARCH_TRANSPORT_ATTEMPTS + 2):
            try:
                result = await _search_call(ctx, req)
                break
            except SearchError as err:
                if err.kind == "rate_limited" and attempt < SEARCH_TRANSPORT_ATTEMPTS + 1:
                    ctx.failures.rate_limit_waits += 1
                    await ctx.warn("rate_limited", target_id=t.id)
                    await ctx.sleep(min(err.retry_after or 2.0, RATE_LIMIT_WAIT_CAP_S))
                    continue
                if (
                    err.kind in ("timeout", "network", "http_error")
                    and attempt < SEARCH_TRANSPORT_ATTEMPTS - 1
                ):
                    ctx.failures.retries += 1
                    await ctx.sleep(0.5 * (2**attempt))
                    continue
                reason = "provider_down" if err.kind in ("auth", "quota") else err.kind
                ctx.failures.record("search", reason, t.id)
                if reason == "provider_down":
                    ctx.provider_down = True
                    ts.reason = "provider_down"
                    await ctx.warn("provider_down", target_id=t.id)
                elif err.kind == "timeout":
                    await ctx.warn("slow_provider", target_id=t.id)
                break
    rec = QueryRecord(
        id=ctx.ids.new("qry"),
        target_id=t.id,
        round=round_no,
        text=fq.text,
        variant=fq.variant,
        language=fq.language,
        country=req.country,
        provider=ctx.search.name,
        exact=fq.exact,
        domain=fq.domain,
        seed=fq.seed,
    )
    if result is None:
        rec.failed = "failed"
        ctx.queries.append(rec)
        return None
    ctx.budget.add_search_cost(result.cost_micros, result.provider)
    rec.results = len(result.hits)
    rec.urls = [canonical_url(h.url) for h in result.hits]
    rec.cost_micros = result.cost_micros
    rec.cached = result.cached
    rec.applied = dict(result.applied)
    rec.locale_weak = result.locale_weak
    ctx.queries.append(rec)
    domains = list(dict.fromkeys(registrable_domain(h.url) for h in result.hits))[:5]
    await ctx.events.emit(
        "search.results",
        target_id=t.id,
        round_=round_no,
        phase="search",
        params={"count": len(result.hits), "domains": ", ".join(domains)},
        detail={"count": len(result.hits), "domains": domains, "query": fq.text},
    )
    return result


# --- triage --------------------------------------------------------------------------------------------


def triage(
    ctx: RunContext, ts: TargetState, fused: list[FusedHit], limit: int
) -> list[tuple[FusedHit, str]]:
    """The best pages for this topic: ``[(hit, source_class)]``, at most ``limit`` and few per domain."""
    t = ts.target
    cfg = ctx.catalog.ceilings
    docs = [[stem(x) for x in tokens(f"{h.hit.title} {h.hit.snippet}")] for h in fused]
    query = [stem(x) for term in t.terms for x in tokens(term)]
    rel = bm25_scores(docs, query)
    top = max(rel, default=0.0) or 1.0
    scored: list[tuple[float, FusedHit, str]] = []
    country = t.place.country_code if t.place else None
    anchor = t.entity or (t.anchors[0] if t.anchors else None)
    for h, r in zip(fused, rel, strict=True):
        if not is_public_url(h.url):
            continue
        cls = classify_source(h.url, ctx.catalog, title=h.hit.title, entity=anchor, country=country)
        authority = (
            1.0
            if ("current" in t.needs or "number" in t.needs) and cls in ("official", "help")
            else 0.0
        )
        score = 2.0 * CLASS_WEIGHT.get(cls, 0.3) + 20.0 * h.score + r / top + authority
        scored.append((score, h, cls))
    scored.sort(key=lambda s: (-round(s[0], 9), s[1].url))
    seen_domains: dict[str, int] = {}
    picks: list[tuple[FusedHit, str]] = []
    done = set(ts.page_ids)
    for _, h, cls in scored:
        page = ctx.pages.get(h.url)
        if page is not None and page.id in done:
            continue
        dom = registrable_domain(h.url)
        if seen_domains.get(dom, 0) >= cfg.max_per_domain:
            continue
        seen_domains[dom] = seen_domains.get(dom, 0) + 1
        picks.append((h, cls))
        if len(picks) >= limit:
            break
    return picks


# --- read ----------------------------------------------------------------------------------------------


async def read_page(
    ctx: RunContext, ts: TargetState, hit: FusedHit, cls: str, round_no: int
) -> PageRecord | None:
    """Read a page once per run, however many topics ask for it at the same moment (one lock per URL)."""
    lock = ctx.page_locks.setdefault(hit.url, asyncio.Lock())
    async with lock:
        cached = ctx.pages.get(hit.url)
        if cached is not None:
            return cached
        return await _read_page(ctx, ts, hit, cls, round_no)


async def _read_page(
    ctx: RunContext, ts: TargetState, hit: FusedHit, cls: str, round_no: int
) -> PageRecord | None:
    t = ts.target
    url = hit.url
    dom = registrable_domain(url)
    title = hit.hit.title
    if hit.hit.content and len(hit.hit.content) >= MIN_PROVIDER_TEXT:
        if not ctx.budget.try_page():
            ctx.budget_stopped = ctx.budget_stopped or bool(ctx.budget.exhausted())
            return None
        text, source, published, final = hit.hit.content, "provider", hit.hit.published, url
        await ctx.events.emit(
            "page.read",
            target_id=t.id,
            round_=round_no,
            phase="read",
            params={"domain": dom},
            detail={"domain": dom, "title": title, "chars": len(text)},
        )
    else:
        if not ctx.active() or ts.cancel.cancelled or not ctx.budget.try_page():
            ctx.budget_stopped = ctx.budget_stopped or bool(ctx.budget.exhausted())
            return None
        await ctx.events.emit(
            "page.reading",
            target_id=t.id,
            round_=round_no,
            phase="read",
            params={"domain": dom},
            detail={"domain": dom, "title": title},
        )
        async with ctx.fetch_sem:
            try:
                page = await asyncio.wait_for(ctx.fetch.fetch(hit.hit.url), timeout=FETCH_TIMEOUT_S)
            except TimeoutError:
                ctx.failures.record("fetch", "timeout", t.id)
                await ctx.events.emit(
                    "page.skipped",
                    target_id=t.id,
                    round_=round_no,
                    phase="read",
                    params={"domain": dom},
                    detail={"domain": dom, "title": title, "reason": "timeout"},
                )
                return None
        if not page.ok:
            reason = page.reason or "error"
            if reason in ("error",):
                ctx.failures.record("fetch", reason, t.id)
            await ctx.events.emit(
                "page.skipped",
                target_id=t.id,
                round_=round_no,
                phase="read",
                params={"domain": dom, "reason": ctx.events.labels.reason(reason)},
                detail={"domain": dom, "title": title or page.title, "reason": reason},
            )
            return None
        text, source, published, final = (
            page.text,
            "fetch",
            page.published or hit.hit.published,
            page.final_url or url,
        )
        title = title or page.title
        await ctx.events.emit(
            "page.read",
            target_id=t.id,
            round_=round_no,
            phase="read",
            params={"domain": dom},
            detail={"domain": dom, "title": title, "chars": len(text)},
        )

    lang = guess_language(text, list(t.place.languages) if t.place else [], ctx.catalog.languages)
    rec = PageRecord(
        id=ctx.ids.new("pg"),
        url=url,
        final_url=canonical_url(final),
        domain=dom,
        source_class=cls,
        title=title,
        text=text,
        content_hash=text_hash(text),
        text_source=source,
        published=published,
        retrieved_on=ctx.as_of,
        language=lang,
    )
    ctx.pages[url] = rec
    ctx.pages_by_id[rec.id] = rec
    return rec


# --- extract and verify -----------------------------------------------------------------------------------


def _register_entity(
    ctx: RunContext, ts: TargetState, name: str, kind: str, page: PageRecord
) -> EntityRecord | None:
    key = clean_key(name)
    if not key or any(own and clean_key(own) == key for own in ctx.input.own_brand_terms):
        return None
    ent = ctx.entities.get(key)
    new = ent is None
    if ent is None:
        ent = EntityRecord(name=name.strip(), kind=kind)
        ctx.entities[key] = ent
    ent.target_ids.add(ts.target.id)
    if own_site(name, page.domain) and not ent.domain:
        ent.domain = page.domain
    return ent if new else None


async def extract_page(
    ctx: RunContext, ts: TargetState, page: PageRecord, round_no: int, cov: Coverage, via_seed: bool
) -> int:
    """Extract and verify from one page; returns the number of newly verified items."""
    t = ts.target
    job = ctx.catalog.jobs[t.job]
    passages = select_passages(
        page.text,
        t.terms,
        max_chars=PASSAGE_CHARS[ctx.input.load.load_class if ctx.input.load else "standard"],
    )
    kinds = list(JOB_KINDS[t.job])
    base = ExtractIn(
        job=t.job,
        question=t.question,
        anchor=t.anchors[0],
        place=t.place.text if t.place else "",
        language=page.language or "en",
        focus=job.extract_focus.strip(),
        kinds=kinds,
        want_kinds=[k for k in unfilled_kinds(ts, cov) if k in kinds],
        kind_guides=[KIND_GUIDE[k] for k in kinds],
        slots=slot_descriptions(ts, cov, ctx) or [s.key for s in t.slots],
        page_title=page.title,
        page_domain=page.domain,
        page_published=page.published.isoformat() if page.published else None,
        passages=render(passages),
        retry_keys=[],
    )
    verified = 0
    pending: list[Candidate | Rejected] = []
    for attempt in range(2):
        inp = (
            base
            if attempt == 0
            else base.model_copy(
                update={
                    "retry_keys": sorted(
                        {c.norm_key for c in pending if isinstance(c, Rejected | Candidate)}
                    )[:12]
                }
            )
        )
        async with ctx.llm_sem:
            res = await ctx.runner.run(EXTRACT_SPECS[t.job], inp)
        ctx.budget.add_llm_cost(res.cost_micros)
        if res.failed:
            ctx.failures.record("llm", res.reason or "failed", t.id)
            await ctx.warn("model_failed", target_id=t.id)
        retry: list[Candidate | Rejected] = []
        existing = {(a.kind, a.norm_key, a.page_id) for a in ts.atoms}
        for cand in candidates_from_output(res.output, page):
            if isinstance(cand, Rejected):
                if cand.reason == "forbidden_pattern":
                    ctx.drops.forbidden_pattern += 1
                await ctx.events.emit(
                    "fact.rejected",
                    target_id=t.id,
                    round_=round_no,
                    phase="verify",
                    detail={"reason": cand.reason, "topic": t.label},
                )
                continue
            if attempt == 1 and cand.norm_key not in {p.norm_key for p in pending}:
                continue  # a retry only answers for the items that failed
            key = (cand.kind, cand.norm_key, page.id)
            if key in existing:
                continue
            await ctx.events.emit(
                "fact.found",
                target_id=t.id,
                round_=round_no,
                phase="verify",
                params={"topic": t.label},
                detail={"kind": cand.kind, "label": cand.label, "topic": t.label},
            )
            bad = check_candidate(cand, page)
            if bad is not None:
                retry.append(cand)
                continue
            existing.add(key)
            atom = AtomRecord(
                id=ctx.ids.new("atm"),
                kind=cand.kind,
                job=t.job,
                target_id=t.id,
                norm_key=cand.norm_key,
                label=cand.label,
                claim=cand.claim,
                quote=cand.quote,
                impact="high"
                if cand.impact == "high" or cand.kind in ctx.catalog.lanes.high_impact_kinds
                else "normal",
                published_on=cand.published_on,
                data=cand.data,
                page_id=page.id,
                round=round_no,
                seed=via_seed,
            )
            ts.atoms.append(atom)
            verified += 1
            for ent_name, ent_kind in _entity_names(cand):
                if _register_entity(ctx, ts, ent_name, ent_kind, page) is not None:
                    await ctx.events.emit(
                        "entity.discovered",
                        target_id=t.id,
                        round_=round_no,
                        phase="verify",
                        params={"name": ent_name},
                        detail={"name": ent_name, "kind": ent_kind},
                    )
            await ctx.events.emit(
                "fact.verified",
                target_id=t.id,
                round_=round_no,
                phase="verify",
                params={"domain": page.domain},
                detail={
                    "kind": cand.kind,
                    "label": cand.label,
                    "domain": page.domain,
                    "topic": t.label,
                },
            )
        pending = retry
        if not pending or not ctx.active():
            break
    for _ in pending:
        ctx.drops.quote_not_found += 1
        await ctx.events.emit(
            "fact.rejected",
            target_id=t.id,
            round_=round_no,
            phase="verify",
            detail={"reason": "quote_not_found", "topic": t.label},
        )
    return verified


def _entity_names(c: Candidate) -> list[tuple[str, str]]:
    data: dict[str, Any] = c.data
    if c.kind == "competitor" and data.get("name"):
        return [(str(data["name"]), "competitor")]
    if c.kind == "service_option" and data.get("provider"):
        return [(str(data["provider"]), "provider")]
    if c.kind == "reference_check" and data.get("name") and data.get("status") == "found":
        return [(str(data["name"]), "reference")]
    return []


# --- one topic -----------------------------------------------------------------------------------------


async def run_topic(
    ctx: RunContext,
    ts: TargetState,
    round_no: int,
    *,
    followups: Sequence[FollowupQuery] | None = None,
    quota: int | None = None,
) -> int:
    """Run one round for one topic; returns the number of newly verified items. Never raises for model or web failures."""
    t = ts.target
    if ts.cancel.cancelled or not ctx.active():
        return 0
    try:
        if ts.status == "queued":
            ts.status = "running"
            await ctx.events.emit(
                "target.started",
                target_id=t.id,
                round_=round_no,
                phase="plan",
                params={"topic": t.label},
                detail={"job": t.job, "lane": t.lane, "round": round_no, "topic": t.label},
            )
        cov = update_coverage(ts)
        want = quota or ctx.catalog.ceilings.variants_per_topic
        if followups is not None:
            names = [e.name for e in ctx.entities.values()]
            variants = [
                f
                for f in followups
                if not is_repeat(f.text, ts.queries)
                and _check(
                    ctx, t, f.text, extra_anchors=names, require_place=False if f.domain else None
                )
            ]
        else:
            variants = await write_variants(ctx, ts, round_no, cov, want)
        if not variants:
            if (
                followups is None
            ):  # no distinct follow-up left is a normal end; no first-round query is a failure
                ctx.failures.record("plan", "no_query", t.id)
            return 0
        results = await asyncio.gather(*[search_one(ctx, ts, v, round_no) for v in variants])
        usable = [(v, r) for v, r in zip(variants, results, strict=True) if r is not None]
        if not usable:
            return 0
        fused = fuse([list(r.hits) for _, r in usable])
        cap = ctx.catalog.ceilings.pages_per_topic
        picks = triage(ctx, ts, fused, cap.max if round_no == 1 else min(cap.max, 3))
        seed_urls = {canonical_url(h.url) for v, r in usable if v.seed for h in r.hits}
        non_seed_urls = {canonical_url(h.url) for v, r in usable if not v.seed for h in r.hits}

        async def process(hit: FusedHit, cls: str) -> int:
            page = await read_page(ctx, ts, hit, cls, round_no)
            if page is None or ts.cancel.cancelled or ctx.run_cancel.cancelled:
                return 0
            if page.id not in ts.page_ids:
                ts.page_ids.append(page.id)
            via_seed = page.url in seed_urls and page.url not in non_seed_urls
            return await extract_page(ctx, ts, page, round_no, cov, via_seed)

        counts = await asyncio.gather(*[process(h, c) for h, c in picks])
        update_coverage(ts)
        return sum(counts)
    except CallCancelled:
        return 0
    except STOP_RUN as exc:
        ctx.budget_stopped = isinstance(exc, BudgetExceeded) or ctx.budget_stopped
        ts.reason = ts.reason or (
            "budget_stop" if isinstance(exc, BudgetExceeded) else "provider_down"
        )
        await ctx.warn("budget_stop")
        return 0
