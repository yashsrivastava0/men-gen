"""Bake-off B1: Tavily versus Serper on queries built from the example Briefs (no hand-written queries).

``python -m mde_research_agent.live_bakeoff [--dry-run | --mock] [--queries 40] [--providers tavily,serper]``

The queries come from the topics the planner derives from the example exports: the first deterministic variant of the
highest-ranked place-bound topics, spread over the jobs and the three exports (plus a local-language variant when the
Brief has one). Each query is sent to each provider with the SAME locale parameters. Measured per provider, on the top 5
results: share from the target country's own domains or official sources, on-topic share (one batched model judgement),
distinct domains, page-text availability (the provider's raw content, or the page fetcher on the provider's links),
latency, cost per query and which locale parameters were really applied. The default provider is the one with the higher
local share; within 5 points, the cheaper one. The report goes to ``docs/research-agent/reports/`` and the raw results
are saved as cassettes.

``--dry-run`` prints the plan and the cost estimate and calls nothing. ``--mock`` runs the plumbing over fake providers.
A real run refuses to start unless the owner's live switches and the search keys are already in the environment: this
module never sets them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml

from mde_core.money import micros_to_usd
from mde_core.paths import main_repo_root
from mde_research_agent.adapters.recording import RecordingSearch
from mde_research_agent.calls import judge as judge_call
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.calls.queries import QueryIn, deterministic_variants
from mde_research_agent.catalog import load_catalog
from mde_research_agent.catalog.loader import DATA_DIR
from mde_research_agent.config import keys_present, load_research_settings
from mde_research_agent.core.countries import countries
from mde_research_agent.core.sources import classify_source
from mde_research_agent.core.urls import registrable_domain
from mde_research_agent.fakes import FakeSearch, FakeWorld
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan
from mde_research_agent.ports.search import SearchPort, SearchRequest, SearchResult

TOP = 5
FETCH_SAMPLE = 3
JUDGE_COST_ESTIMATE_MICROS = 20_000
REPO = main_repo_root() or Path(__file__).resolve().parents[4]
DEFAULT_EXPORTS = REPO / "docs" / "compass" / "research-inputs"
REPORT_DIR = REPO / "docs" / "research-agent" / "reports"
CASSETTES = REPO / "agents" / "research" / "tests" / "cassettes"


@dataclass
class Probe:
    id: str
    export: str
    target: str
    job: str
    request: SearchRequest
    country: str | None


@dataclass
class ProviderStats:
    name: str
    results: dict[str, SearchResult | None] = field(default_factory=dict)
    latency_ms: list[int] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    fetch_ok: int = 0
    fetch_tried: int = 0
    on_topic: int = 0
    judged: int = 0


def preflight_live(environ: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ) if environ is None else environ
    problems = []
    if env.get("LLM_MODE", "").strip().lower() != "live":
        problems.append(
            "the model mode must be live in the environment (the judge is one model call)"
        )
    if env.get("ALLOW_LIVE_LLM", "").strip() != "1":
        problems.append(
            "the second key (ALLOW_LIVE_LLM=1) must be set in the environment by the owner"
        )
    if env.get("MDE_FORBID_LIVE", "").strip() in ("1", "true"):
        problems.append("MDE_FORBID_LIVE is set")
    return problems


def build_probes(exports_dir: Path, n: int, proposals: dict[str, Any] | None = None) -> list[Probe]:
    """About ``n`` queries, generically derived: first descriptive variant (and a local one) of place-bound topics."""
    cat = load_catalog()
    files = sorted(exports_dir.glob("*.json"))
    per = max(1, -(-n // max(1, len(files))))
    probes: list[Probe] = []
    for f in files:
        inp = read_export(json.loads(f.read_text(encoding="utf-8")), cat)
        plan = build_plan(inp, cat, proposal=(proposals or {}).get(f.stem))
        picked: list[Any] = []
        seen_jobs: dict[str, int] = {}
        for t in plan.targets + plan.unrun:  # spread over jobs: at most two per job first
            if t.place is None:
                continue
            if seen_jobs.get(t.job, 0) < 2:
                picked.append(t)
                seen_jobs[t.job] = seen_jobs.get(t.job, 0) + 1
        for t in picked:
            if len([p for p in probes if p.export == f.stem]) >= per:
                break
            langs = list(t.place.languages[:1])
            qi = QueryIn(
                job=t.job,
                question=t.question,
                anchors=list(t.anchors),
                context=plan.category_phrase,
                place=t.place.text,
                languages=langs,
                local_terms=[s for s in inp.hints.synonyms if not s.isascii()][:3],
                local_intent=list(
                    words.words[:3]
                    if langs and (words := cat.intent_words.languages.get(langs[0]))
                    else []
                ),
                intent=list(t.intent),
                entity=t.entity,
                earlier=[],
                want=2,
                round=1,
                focus_terms=[],
                seed_names=[],
            )
            for v in deterministic_variants(qi).variants[:2]:
                if len([p for p in probes if p.export == f.stem]) >= per:
                    break
                probes.append(
                    Probe(
                        id=f"P{len(probes) + 1:02d}",
                        export=f.stem,
                        target=t.label,
                        job=t.job,
                        country=t.place.country_code,
                        request=SearchRequest(
                            query=v.text,
                            country=t.place.country_code,
                            language=v.language,
                            exact=v.exact,
                            recency_months=t.recency_months,
                            want_content=True,
                            max_results=10,
                        ),
                    )
                )
    return probes[:n]


def _is_local(url: str, country: str | None, cat: Any) -> bool:
    dom = registrable_domain(url)
    tld = (countries().get(country or "", {}) or {}).get("tld", "")
    if tld and (
        dom.endswith("." + tld)
        or dom.endswith(f".co.{tld}")
        or dom.endswith(f".gov.{tld}")
        or dom.endswith(f".com.{tld}")
    ):
        return True
    return (
        classify_source(url, cat, country=country) in ("official",)
        and bool(tld)
        and any(
            e.country == (country or "") and (dom == e.domain or dom.endswith("." + e.domain))
            for e in cat.authority
        )
    )


async def run_provider(
    provider: SearchPort, probes: list[Probe], concurrency: int = 4
) -> ProviderStats:
    stats = ProviderStats(name=provider.name)
    sem = asyncio.Semaphore(concurrency)

    async def one(p: Probe) -> None:
        async with sem:
            t0 = time.monotonic()
            try:
                res = await provider.search(p.request)
                stats.results[p.id] = res
                stats.latency_ms.append(res.latency_ms or int((time.monotonic() - t0) * 1000))
            except Exception as exc:  # a provider failure is a result of the bake-off, not a crash
                stats.results[p.id] = None
                stats.errors[p.id] = f"{type(exc).__name__}: {str(exc)[:120]}"

    await asyncio.gather(*[one(p) for p in probes])
    return stats


async def measure_page_text(stats: ProviderStats, client: httpx.AsyncClient) -> None:
    """Page-text availability: the provider's own raw content, else the page fetcher on its top links."""
    from mde_research_agent.adapters.fetch.http import HttpFetcher

    fetcher = HttpFetcher(client=client)
    for res in stats.results.values():
        if res is None:
            continue
        for hit in res.hits[:FETCH_SAMPLE]:
            stats.fetch_tried += 1
            if hit.content and len(hit.content) >= 300:
                stats.fetch_ok += 1
                continue
            page = await fetcher.fetch(hit.url)
            stats.fetch_ok += 1 if page.ok else 0


async def judge(stats_list: list[ProviderStats], probes: list[Probe], runner: CallRunner) -> None:
    for stats in stats_list:
        queries = []
        for p in probes:
            res = stats.results.get(p.id)
            if res is None:
                continue
            queries.append(
                judge_call.JudgeQuery(
                    id=p.id,
                    query=p.request.query,
                    results=[
                        judge_call.JudgeResult(title=h.title, snippet=h.snippet)
                        for h in res.hits[:TOP]
                    ],
                )
            )
        for i in range(0, len(queries), 20):
            r = await runner.run(judge_call.SPEC, judge_call.JudgeIn(queries=queries[i : i + 20]))
            for v in r.output.verdicts:
                stats.judged += len(v.on_topic)
                stats.on_topic += sum(1 for x in v.on_topic if x)


def summarize(stats: ProviderStats, probes: list[Probe], cat: Any) -> dict[str, Any]:
    ok = [(p, stats.results[p.id]) for p in probes if stats.results.get(p.id) is not None]
    top = [(p, h) for p, r in ok for h in r.hits[:TOP]]  # type: ignore[union-attr]
    local = sum(1 for p, h in top if _is_local(h.url, p.country, cat))
    domains = {registrable_domain(h.url) for _, h in top}
    cost = sum(r.cost_micros for _, r in ok)  # type: ignore[union-attr]
    applied_country = sum(1 for p, r in ok if p.country and "country" in r.applied)  # type: ignore[union-attr]
    country_asked = sum(1 for p, _ in ok if p.country)
    return {
        "provider": stats.name,
        "queries": len(probes),
        "answered": len(ok),
        "failed": len(probes) - len(ok),
        "errors": sorted(set(stats.errors.values()))[:5],
        "local_share": round(local / len(top), 4) if top else 0.0,
        "on_topic_share": round(stats.on_topic / stats.judged, 4) if stats.judged else None,
        "distinct_domains_per_query": round(len(domains) / len(ok), 2) if ok else 0,
        "page_text_available": round(stats.fetch_ok / stats.fetch_tried, 4)
        if stats.fetch_tried
        else None,
        "latency_ms_mean": round(sum(stats.latency_ms) / len(stats.latency_ms))
        if stats.latency_ms
        else None,
        "cost_per_query_usd": str(
            (Decimal(micros_to_usd(cost)) / max(1, len(ok))).quantize(Decimal("0.00001"))
        ),
        "total_cost_usd": str(Decimal(micros_to_usd(cost)).quantize(Decimal("0.0001"))),
        "country_applied_share": round(applied_country / country_asked, 4)
        if country_asked
        else None,
        "locale_weak": sum(1 for _, r in ok if r.locale_weak),  # type: ignore[union-attr]
    }


def choose(summaries: list[dict[str, Any]]) -> tuple[str, str]:
    """Higher local share wins; within 5 points the cheaper one."""
    live = [s for s in summaries if s["answered"]]
    if not live:
        return "none", "no provider answered"
    if len(live) == 1:
        return live[0]["provider"], f"only {live[0]['provider']} answered"
    a, b = sorted(live, key=lambda s: -s["local_share"])[:2]
    if a["local_share"] - b["local_share"] > 0.05:
        return a[
            "provider"
        ], f"higher local share ({a['local_share']:.0%} vs {b['local_share']:.0%})"
    cheaper = min((a, b), key=lambda s: Decimal(s["cost_per_query_usd"]))
    return (
        cheaper["provider"],
        f"local share within 5 points ({a['local_share']:.0%} vs {b['local_share']:.0%}); the cheaper one",
    )


def report_text(
    summaries: list[dict[str, Any]],
    chosen: tuple[str, str],
    probes: list[Probe],
    day: date,
    judge_cost: str,
) -> str:
    cols = [s["provider"] for s in summaries]
    rows = [
        ("Queries answered / sent", lambda s: f"{s['answered']} / {s['queries']}"),
        (
            "Share of top-5 from the country's own or official domains",
            lambda s: f"{s['local_share']:.1%}",
        ),
        (
            "On-topic share of top-5 (model judge)",
            lambda s: "n/a" if s["on_topic_share"] is None else f"{s['on_topic_share']:.1%}",
        ),
        ("Distinct domains per query (top 5)", lambda s: str(s["distinct_domains_per_query"])),
        (
            "Page text available (top 3 hits)",
            lambda s: (
                "n/a" if s["page_text_available"] is None else f"{s['page_text_available']:.1%}"
            ),
        ),
        ("Mean latency (ms)", lambda s: str(s["latency_ms_mean"])),
        ("Cost per query (USD)", lambda s: s["cost_per_query_usd"]),
        (
            "Country parameter applied",
            lambda s: (
                "n/a" if s["country_applied_share"] is None else f"{s['country_applied_share']:.1%}"
            ),
        ),
        ("Results flagged locale_weak", lambda s: str(s["locale_weak"])),
        ("Errors", lambda s: "; ".join(s["errors"]) or "none"),
    ]
    out = [
        f"# Search provider bake-off, {day.isoformat()}",
        "",
        f"{len(probes)} queries, built generically from the topics the planner derives from the example Briefs (no hand-written queries), sent to each provider with the same locale parameters. Judge cost: USD {judge_cost}.",
        "",
    ]
    out += ["| Measure | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    out += [f"| {label} | " + " | ".join(fn(s) for s in summaries) + " |" for label, fn in rows]
    out += [
        "",
        f"**Default provider: {chosen[0]}** ({chosen[1]}). The rule: higher local share wins; within 5 points the cheaper one; the other stays as the configured alternative.",
        "",
        "## Queries used",
        "",
        "| id | export | job | query | country | language |",
        "|---|---|---|---|---|---|",
    ]
    out += [
        f"| {p.id} | {p.export} | {p.job} | {p.request.query} | {p.country or ''} | {p.request.language} |"
        for p in probes
    ]
    return "\n".join(out) + "\n"


def _read_exports(exports_dir: Path) -> list[tuple[str, str]]:
    return [(f.stem, f.read_text(encoding="utf-8")) for f in sorted(exports_dir.glob("*.json"))]


async def infer_countries(exports_dir: Path, mock: bool) -> dict[str, Any]:
    """One planner call per export (the real pipeline's way to find a country the Brief does not state)."""
    from mde_core.settings import load_settings
    from mde_research_agent.calls import plan as plan_call
    from mde_research_agent.compose import build_service
    from mde_research_agent.engine.director import build_plan_in, proposal_from

    cat = load_catalog()
    core = load_settings(llm_mode="mock") if mock else load_settings()
    svc = build_service(memory=True, core_settings=core, provider="fake" if mock else None)
    out: dict[str, Any] = {}
    try:
        runner = CallRunner(svc.gateway, run_id=svc.ids.new("rrn"))
        for stem, text in await asyncio.to_thread(_read_exports, exports_dir):
            inp = read_export(json.loads(text), cat)
            res = await runner.run(plan_call.SPEC, build_plan_in(inp, cat))
            out[stem] = proposal_from(res.output)
    finally:
        await svc.aclose()
    return out


def estimate(probes: list[Probe], providers: list[str]) -> dict[str, Any]:
    import yaml as _y

    prices = _y.safe_load((DATA_DIR / "provider_prices.yaml").read_text(encoding="utf-8"))
    per_call = {"tavily": 8_000, "serper": 1_000}
    for name in providers:
        block = (
            (prices.get("providers", prices) or {}).get(name, {})
            if isinstance(prices, dict)
            else {}
        )
        if isinstance(block, dict) and isinstance(block.get("cost_micros"), int):
            per_call[name] = block["cost_micros"]
    search = sum(per_call.get(p, 8_000) * len(probes) for p in providers)
    return {
        "queries": len(probes),
        "providers": providers,
        "search_usd": str(Decimal(micros_to_usd(search)).quantize(Decimal("0.001"))),
        "judge_usd": str(
            Decimal(micros_to_usd(JUDGE_COST_ESTIMATE_MICROS * len(providers))).quantize(
                Decimal("0.001")
            )
        ),
    }


async def run(args: argparse.Namespace) -> int:
    cat = load_catalog()
    proposals = None if args.dry_run else await infer_countries(args.exports, args.mock)
    probes = build_probes(args.exports, args.queries, proposals)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    if args.dry_run:
        sys.stdout.write(
            json.dumps(
                {
                    "plan": estimate(probes, providers),
                    "sample": [p.request.query for p in probes[:8]],
                },
                indent=1,
            )
            + "\n"
        )
        return 0
    cfg = load_research_settings()
    day = datetime.now(UTC).date()
    cassettes = CASSETTES / f"bakeoff-{day:%Y%m%d}"
    ports: list[SearchPort] = []
    client = httpx.AsyncClient(timeout=15.0, trust_env=False)
    if args.mock:
        for name in providers:
            fake = FakeSearch(FakeWorld())
            fake.name = name
            ports.append(fake)
        core = None
    else:
        problems = preflight_live()
        keys = keys_present(cfg)
        providers = [p for p in providers if keys.get(p)]
        if not providers:
            problems.append("no search key is set for the requested providers")
        if problems:
            sys.stderr.write("cannot run the bake-off: " + "; ".join(problems) + "\n")
            return 2
        from mde_research_agent.adapters.search.serper import SerperAdapter
        from mde_research_agent.adapters.search.tavily import TavilyAdapter

        for name in providers:
            if name == "tavily" and cfg.tavily_api_key:
                ports.append(
                    RecordingSearch(
                        TavilyAdapter(cfg.tavily_api_key.get_secret_value(), client=client),
                        cassettes,
                    )
                )
            elif name == "serper" and cfg.serper_api_key:
                ports.append(
                    RecordingSearch(
                        SerperAdapter(cfg.serper_api_key.get_secret_value(), client=client),
                        cassettes,
                    )
                )
        from mde_core.settings import load_settings

        core = load_settings()
    stats = [await run_provider(p, probes) for p in ports]
    if not args.mock:
        for st in stats:
            await measure_page_text(st, client)
    from mde_research_agent.compose import build_service

    svc = build_service(
        memory=True,
        core_settings=core if core is not None else None,
        provider="fake" if args.mock else None,
    )
    try:
        runner = CallRunner(svc.gateway, run_id=svc.ids.new("rrn"))
        await judge(stats, probes, runner)
        judge_micros = sum(r.cost_micros for _, r in runner.calls)
    finally:
        await svc.aclose()
        await client.aclose()
    summaries = [summarize(s, probes, cat) for s in stats]
    chosen = choose(summaries)
    text = report_text(
        summaries,
        chosen,
        probes,
        day,
        str(Decimal(micros_to_usd(judge_micros)).quantize(Decimal("0.0001"))),
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"bakeoff-{day.isoformat()}{'-mock' if args.mock else ''}.md"
    out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    if args.write_default and chosen[0] in ("tavily", "serper") and not args.mock:
        (DATA_DIR / "providers.yaml").write_text(
            yaml.safe_dump({"default": chosen[0]}), encoding="utf-8"
        )
        sys.stdout.write(f"wrote data/providers.yaml default: {chosen[0]}\n")
    sys.stdout.write(f"report: {out}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mde_research_agent.live_bakeoff", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--exports", type=Path, default=DEFAULT_EXPORTS)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--providers", default="tavily,serper")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument(
        "--write-default", action="store_true", help="write the winner to data/providers.yaml"
    )
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
