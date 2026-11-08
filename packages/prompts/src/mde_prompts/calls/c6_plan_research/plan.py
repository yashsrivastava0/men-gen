"""Deterministic research plan from the Brief (no model).

Used by the C6 mock handler and as the engine's registered fallback ``c6_deterministic_plan``.
Queries are built from generic terms (kind of app, region, feature and service types, the sensitive
area). Names enter only R2 (reference apps) and R3 (the user's own site), and only when the policy
allows company names in queries. All wording lives in ``fixtures/query_templates.yaml``.

How many queries one job may carry is derived from the depth cap and the number of web jobs that
apply, by :func:`queries_per_job` (D-078). The research engine calls that one function, passes the
result to C6 as ``max_queries_per_job`` and validates the plan with it; this planner only reads
``C6Input.max_queries_per_job``, so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache
from itertools import zip_longest
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from mde_contracts.calls import BriefContext, C6Input, C6Output, ResearchJobPlan
from mde_contracts.research import ResearchJobId

TEMPLATES = Path(__file__).parent / "fixtures" / "query_templates.yaml"
SKIPPED_FLAGS = frozenset({"needs_expert_review"})
_JOB_ORDER: tuple[ResearchJobId, ...] = ("R1", "R2", "R3", "R4", "R5", "R6")
FEATURE_LABEL_CHARS = 40


@lru_cache(maxsize=1)
def templates() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(TEMPLATES.read_text(encoding="utf-8")))


def web_jobs(jobs: Iterable[str]) -> list[str]:
    """The web jobs (R1 to R5) among ``jobs``; R6 reads company documents and never uses the web."""
    return [job for job in jobs if job != "R6"]


def queries_per_job(depth_cap: int, active_web_jobs: int) -> int:
    """How many queries one web job may carry: ``clamp(ceil(depth_cap / jobs), min, max)``.

    The single rule behind ``C6Input.max_queries_per_job`` (D-078, ``queries_per_job`` in the query
    templates): the research engine uses it to build the C6 input and to validate the plan. Light
    (a cap up to ``fixed_up_to_cap``, 6) always gets the minimum of 2 per job; with five jobs
    Standard (12) gets 3 and Deep (20) gets 4. When a policy bars company names only R1, R4 and R5
    apply and each job may carry more (Standard 4, Deep 7). The depth cap still limits the plan's
    total, so a larger per-job number never raises the number of searches.
    """
    rule = cast("dict[str, int]", templates()["queries_per_job"])
    low, high = int(rule["min"]), int(rule["max"])
    if depth_cap <= int(rule["fixed_up_to_cap"]):
        return low
    wanted = -(-max(0, depth_cap) // max(1, active_web_jobs))  # ceiling division
    return max(low, min(high, wanted))


def _clean(text: str, limit: int = 80) -> str:
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:limit]


def topic_phrase(archetype: str) -> str:
    """Generic phrase for the kind of app (never a company name)."""
    tpl = templates()
    phrase = cast("str", cast("dict[str, str]", tpl["topics"]).get(archetype))
    if phrase is None:
        phrase = archetype.replace("_", " ").replace("-", " ").strip()
    return _clean(phrase) or cast("str", tpl["fallback_topic"])


def region_suffix(regions: list[str]) -> str:
    names = cast("dict[str, str]", templates()["regions"])
    for region in regions:
        name = names.get(region.casefold())
        if name:
            return f" in {name}"
    return ""


def brand_host(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").casefold()
    host = host.removeprefix("www.")
    return host or None


def integration_topics(brief: BriefContext) -> list[str]:
    """Service types the Brief implies (payments, push, maps, sign-in ...), in a stable order."""
    tpl = templates()
    by_feature = cast("dict[str, str]", tpl["integration_features"])
    by_login = cast("dict[str, str]", tpl["integration_logins"])
    topics: list[str] = []
    for feature in brief.features:
        topic = by_feature.get(feature.id)
        if topic and topic not in topics:
            topics.append(topic)
    login = by_login.get(brief.login.casefold()) if brief.login else None
    if login and login not in topics:
        topics.append(login)
    # sign-in and payments first: they are the services most likely to need an SDK decision
    ranked = sorted(
        enumerate(topics),
        key=lambda item: (0 if "sign-" in item[1] else 1 if "payment" in item[1] else 2, item[0]),
    )
    topics = [t for _, t in ranked]
    return topics


def _compliance_topics(brief: BriefContext) -> list[str]:
    names = cast("dict[str, str]", templates()["compliance_topics"])
    topics: list[str] = []
    for flag in brief.compliance_flags:
        if flag in SKIPPED_FLAGS:
            continue
        topic = names.get(flag, flag.replace("_", " "))
        if topic not in topics:
            topics.append(topic)
    return topics


def feature_names(brief: BriefContext) -> list[str]:
    """The names of the Brief's features as short plain phrases, each once (for R1 queries)."""
    seen: set[str] = set()
    names: list[str] = []
    for feature in brief.features:
        name = _clean(feature.label, FEATURE_LABEL_CHARS)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def _fill(template: str, **values: str) -> str:
    return " ".join(template.format(**values).split())


def _dedupe(queries: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        key = " ".join(query.casefold().split())
        if key not in seen:
            seen.add(key)
            out.append(query)
    return out


def _expand(
    job_templates: Sequence[str], key: str, subjects: Sequence[str], common: dict[str, str]
) -> list[str]:
    """Every template for every subject, angle by angle: the first template of all subjects, then
    the second template of all subjects ..., so each subject is covered before any is repeated."""
    return _dedupe(
        _fill(template, **{key: subject}, **common)
        for template in job_templates
        for subject in subjects
    )


def _r1_queries(job: dict[str, Any], common: dict[str, str], features: Sequence[str]) -> list[str]:
    """R1: the lead generic queries, then one query per Brief feature alternating with the rest of
    the generic angles (so a small cap still sees both the Brief and the wider market)."""
    generic = [_fill(q, **common) for q in cast("list[str]", job["queries"])]
    lead = int(job["lead_queries"])
    per_feature = _expand(cast("list[str]", job["feature_queries"]), "feature", features, common)
    merged = list(generic[:lead])
    for from_feature, from_generic in zip_longest(per_feature, generic[lead:]):
        if from_feature:
            merged.append(from_feature)
        if from_generic:
            merged.append(from_generic)
    return _dedupe(merged)


def candidate_queries(inp: C6Input) -> dict[ResearchJobId, list[str]]:
    """Every query the Brief justifies, before trimming (at most ``max_queries_per_job`` per job)."""
    tpl = templates()
    jobs = cast("dict[str, dict[str, Any]]", tpl["jobs"])
    cap = inp.max_queries_per_job
    brief = inp.brief
    allow_names = inp.policy.allow_company_name_in_queries
    available = set(inp.jobs_available)
    topic = topic_phrase(brief.archetype)
    common = {"topic": topic, "region": region_suffix(brief.regions)}
    out: dict[ResearchJobId, list[str]] = {}

    def queries_of(job: str) -> list[str]:
        return cast("list[str]", jobs[job]["queries"])

    if "R1" in available:
        out["R1"] = _r1_queries(jobs["R1"], common, feature_names(brief))[:cap]
    if "R2" in available and allow_names and brief.references:
        names = [_clean(r, 60) for r in brief.references if _clean(r, 60)]
        out["R2"] = _expand(queries_of("R2"), "reference", names, common)[:cap]
    host = brand_host(brief.brand_url)
    if "R3" in available and allow_names and host:
        out["R3"] = _expand(queries_of("R3"), "brand_host", [host], common)[:cap]
    if "R4" in available:
        topics = _compliance_topics(brief)
        if topics:
            out["R4"] = _expand(queries_of("R4"), "compliance_topic", topics, common)[:cap]
    if "R5" in available:
        topics = integration_topics(brief)
        if topics:
            out["R5"] = _expand(queries_of("R5"), "integration_topic", topics, common)[:cap]
    if "R6" in available:
        out["R6"] = [_fill(queries_of("R6")[0], **common)]
    return {k: v for k, v in out.items() if v}


def trim(
    candidates: dict[ResearchJobId, list[str]],
    max_web_queries: int,
    priority: list[str],
    per_job: int | None = None,
) -> dict[ResearchJobId, list[str]]:
    """Keep at most ``max_web_queries`` web queries and ``per_job`` per job (default: the largest
    per-job number a plan may carry): every job's first query before any second one, every second
    before any third ..., in ``priority`` order. R6 (internal, no web) is never counted or dropped."""
    limit = per_job if per_job is not None else int(templates()["queries_per_job"]["max"])
    order: list[ResearchJobId] = sorted(
        (j for j in candidates if j != "R6"),
        key=lambda j: priority.index(j) if j in priority else len(priority),
    )
    chosen: dict[ResearchJobId, int] = {}
    budget = max(0, max_web_queries)
    for position in range(max(0, limit)):
        for job in order:
            if budget <= 0:
                break
            if len(candidates[job]) > position:
                chosen[job] = position + 1
                budget -= 1
    out: dict[ResearchJobId, list[str]] = {}
    for job in _JOB_ORDER:
        if job == "R6":
            if job in candidates:
                out[job] = list(candidates[job])
        elif chosen.get(job):
            out[job] = candidates[job][: chosen[job]]
    return out


def deterministic_plan(inp: C6Input) -> C6Output:
    """The plan a careful human would write from the Brief alone, trimmed to cap and budget."""
    tpl = templates()
    per_search = max(1, int(tpl["search_cost_micros"]))
    by_budget = max(0, inp.remaining_budget_micros) // per_search
    allowed = min(max(0, inp.max_search_calls), by_budget)
    trimmed = trim(
        candidate_queries(inp),
        allowed,
        cast("list[str]", tpl["job_priority"]),
        per_job=inp.max_queries_per_job,
    )
    rationales = {
        k: str(v["rationale"]) for k, v in cast("dict[str, dict[str, Any]]", tpl["jobs"]).items()
    }
    plans = [
        ResearchJobPlan(job=job, rationale=rationales[job], queries=queries)
        for job, queries in trimmed.items()
    ]
    return C6Output(jobs=plans)
