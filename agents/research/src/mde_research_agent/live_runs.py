"""Batch B2: end-to-end Research runs on the example Briefs and a synthetic one.

``python -m mde_research_agent.live_runs --run quick|standard|thorough|synthetic|all [--dry-run | --mock | --replay] [--record]``

For each selected Brief it runs the whole pipeline (plan, rounds, checker, assembly, validators, commit) through the same
service the API uses, then writes ``research.json``, the event log, a truncated evidence file and a metrics summary to
``agents/research/examples/<name>/`` and a one-page summary to ``docs/research-agent/examples/<name>.md``.

* ``--dry-run`` prints the plan, ceilings and a cost estimate for each run and calls nothing.
* ``--mock`` runs over fake search and fetch with mock models (tests the plumbing offline).
* ``--replay`` serves the recorded cassettes (never goes live).
* A real run refuses to start unless the owner's live switches and a search key are ALREADY in the environment (this module
  never sets them). It checks the allowance (``--allowance-usd``) and the shared ledger before EACH run and, when the
  allowance is nearly used, finishes the run in progress and starts no new one. ``--record`` saves search, fetch and model
  cassettes so a run can be replayed offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mde_core.money import micros_to_usd, usd_to_micros
from mde_core.paths import main_repo_root
from mde_core.settings import load_settings
from mde_research_agent.adapters.recording import (
    RecordingFetch,
    RecordingSearch,
    ReplayFetch,
    ReplaySearch,
)
from mde_research_agent.catalog import load_catalog
from mde_research_agent.config import keys_present, load_research_settings
from mde_research_agent.devtools import fake_world, script_service
from mde_research_agent.engine.spend import research_spent_micros
from mde_research_agent.intake import read_export
from mde_research_agent.metrics import run_metrics
from mde_research_agent.plan.build import build_plan

REPO = main_repo_root() or Path(__file__).resolve().parents[4]
EXAMPLES = REPO / "agents" / "research" / "examples"
SUMMARIES = REPO / "docs" / "research-agent" / "examples"
CASSETTES = REPO / "agents" / "research" / "tests" / "cassettes"
INPUTS = REPO / "docs" / "compass" / "research-inputs"
SYNTHETIC = REPO / "agents" / "research" / "tests" / "fixtures" / "synthetic"
RUNS = ("quick", "standard", "thorough", "synthetic")


def example_path(name: str, folder: Path | None = None) -> Path:
    """The export for one run: the example of that tier in the inputs folder (found by its tier prefix), or the synthetic Brief."""
    if name == "synthetic":
        return SYNTHETIC / "dental-clinic-dubai-ar-en.json"
    found = sorted((folder or INPUTS).glob(f"{name}-*.json"))
    if not found:
        raise SystemExit(f"no {name}-*.json export in {folder or INPUTS}")
    return found[0]


EVIDENCE_TEXT_CHARS = 2000
TARGETS = {
    "quote_check_pass": (">=", 1.0),
    "slots_filled_ratio": (">=", 0.70),
    "searches_contributing_ratio": (">=", 0.60),
    "norms_with_3_plus_sources_ratio": (">=", 0.70),
    "recent_24m_ratio_of_dated": (">=", 0.70),
    "high_impact_corroborated_or_flagged": (">=", 1.0),
}


def preflight_live(environ: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ) if environ is None else environ
    problems = []
    if env.get("LLM_MODE", "").strip().lower() != "live":
        problems.append("the model mode must be live in the environment")
    if env.get("ALLOW_LIVE_LLM", "").strip() != "1":
        problems.append(
            "the second key (ALLOW_LIVE_LLM=1) must be set in the environment by the owner"
        )
    if env.get("MDE_FORBID_LIVE", "").strip() in ("1", "true"):
        problems.append("MDE_FORBID_LIVE is set")
    if not any(keys_present(load_research_settings()).values()):
        problems.append("no search key is set (TAVILY_API_KEY or SERPER_API_KEY)")
    return problems


def estimate_run(export: dict[str, Any], tavily_micros: int = 8_000) -> dict[str, Any]:
    cat = load_catalog()
    inp = read_export(export, cat)
    plan = build_plan(inp, cat)
    cs = cat.ceilings.for_class(inp.load.load_class if inp.load else "standard")
    search_usd = Decimal(micros_to_usd(cs.soft.searches * tavily_micros))
    llm_usd = Decimal(
        micros_to_usd(cs.soft.pages * 2_500 + len(plan.targets) * 6_000)
    )  # about 2.5k and 6k micro-dollars each
    return {
        "load": [inp.load.score, inp.load.load_class] if inp.load else None,
        "targets": len(plan.targets),
        "unrun": len(plan.unrun),
        "soft": cs.soft.model_dump(),
        "hard": cs.hard.model_dump(),
        "estimate_usd": str((search_usd + llm_usd).quantize(Decimal("0.001"))),
        "plan_source": plan.plan_source,
    }


def truncate_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    ev = json.loads(json.dumps(evidence))
    for p in ev.get("pages", []):
        if len(p.get("text", "")) > EVIDENCE_TEXT_CHARS:
            p["text"] = p["text"][:EVIDENCE_TEXT_CHARS] + " [truncated]"
    ev["events"] = f"{len(evidence.get('events', []))} events (see events.jsonl)"
    return ev


def verdicts(metrics: dict[str, Any]) -> dict[str, str]:
    out = {}
    for key, (op, want) in TARGETS.items():
        got = metrics.get(key)
        out[key] = (
            "n/a"
            if got is None
            else ("pass" if (got >= want if op == ">=" else got <= want) else "FAIL")
        )
    return out


def render_summary(name: str, research: dict[str, Any], metrics: dict[str, Any], mode: str) -> str:
    b, p, c = research["budget"], research["profile"], research["coverage"]
    v = verdicts(metrics)
    lines = [
        f"# Research run: {name} ({mode})",
        "",
        f"Status **{research['status']}**, load {p['load_score']} ({p['load_class']}), {p['rounds']} round(s). "
        f"{b['searches_used']} searches, {b['pages_read']} pages, {b['seconds']} s, USD {b['usd']}, {b['failures']} failures. "
        f"{len(research['items'])} items, {len(research['norms'])} norms, {len(research['confirm'])} to confirm, {len(research['conflicts'])} conflicts.",
        "",
        "## Targets",
        "",
        "| Measure | Result | Target | |",
        "|---|---|---|---|",
    ]
    for key, (op, want) in TARGETS.items():
        got = metrics.get(key)
        lines.append(
            f"| {key} | {'n/a' if got is None else f'{got:.1%}'} | {op} {want:.0%} | {v[key]} |"
        )
    lines += [
        "",
        f"Coverage: {c['covered']} of {c['targets']} topics covered, {c['not_found']} not found; {c['slots_filled']} of {c['slots_required']} required details filled.",
        "",
    ]
    if metrics["failed_slots"]:
        lines += (
            ["Unfilled required details:", ""]
            + [
                f"- {s['target']} {s['label']} ({s['status']}, {s['filled']}/{s['required']})"
                for s in metrics["failed_slots"][:15]
            ]
            + [""]
        )
    lines += [
        "## What is usual (top norms)",
        "",
        "| Level | Seen in | Area | Norm | Newest source |",
        "|---|---|---|---|---|",
    ]
    order = {"core": 0, "common": 1, "emerging": 2, "advanced": 3, "limited_evidence": 4}
    for n in sorted(
        research["norms"],
        key=lambda n: (order[n["prevalence"]["level"]], -n["prevalence"]["seen_in"], n["key"]),
    )[:25]:
        newest = n["recency"]["newest_source"] or "n/a"
        lines.append(
            f"| {n['prevalence']['level']} | {n['prevalence']['seen_in']}/{n['prevalence']['of']} | {n['area']} | {n['label']} | {newest} |"
        )
    for lane in ("blueprint", "integrations", "design", "screens", "mock", "verify"):
        digest = research["slices"].get(lane, {}).get("digest", "")
        if digest:
            lines += [
                "",
                f"## Lane digest: {lane}",
                "",
                "```",
                digest[:1800] + ("\n..." if len(digest) > 1800 else ""),
                "```",
            ]
    lines += ["", "## Gaps", ""] + [
        f"- {g['target_id']} {g['reason']}: {g['label']}" for g in research["gaps"][:25]
    ]
    lines += [
        "",
        "## Counts",
        "",
        f"By kind: {metrics['by_kind']}",
        "",
        f"Flags: {metrics['flags']}",
        "",
        f"Source classes: {metrics['source_classes']}",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(
    name: str,
    mode: str,
    research: dict[str, Any],
    evidence: dict[str, Any],
    events: list[str],
    metrics: dict[str, Any],
    out_root: Path = EXAMPLES,
    summary_root: Path = SUMMARIES,
) -> Path:
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "research.json").write_text(
        json.dumps(research, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "events.jsonl").write_text("".join(line + "\n" for line in events), encoding="utf-8")
    (out / "evidence.json").write_text(
        json.dumps(truncate_evidence(evidence), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "summary.json").write_text(
        json.dumps(
            {"mode": mode, "metrics": metrics, "verdicts": verdicts(metrics)},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary_root.mkdir(parents=True, exist_ok=True)
    (summary_root / f"{name}.md").write_text(
        render_summary(name, research, metrics, mode), encoding="utf-8"
    )
    return out


def destinations(args: argparse.Namespace) -> tuple[Path, Path]:
    """Real examples and one-pagers only for a live run; mock and replay go to a temp folder unless --out says otherwise."""
    if args.out:
        return args.out / "examples", args.out / "summaries"
    if args.mock or args.replay:
        scratch = Path(tempfile.gettempdir()) / "mde-research-offline"
        return scratch / "examples", scratch / "summaries"
    return EXAMPLES, SUMMARIES


async def run_one(
    name: str,
    export: dict[str, Any],
    args: argparse.Namespace,
    spent_before: int,
    allowance_micros: int,
) -> dict[str, Any]:
    mode = "mock" if args.mock else "replay" if args.replay else "live"
    cass = CASSETTES / f"research-{name}"
    core = (
        load_settings(llm_mode="mock")
        if args.mock
        else load_settings(llm_mode="replay", cassette_dir=cass / "llm")
        if args.replay
        else load_settings(
            **({"record_cassettes": True, "cassette_dir": cass / "llm"} if args.record else {})
        )
    )
    wrap_search = wrap_fetch = None
    provider = "fake" if args.mock else None
    if args.replay:
        provider = "fake"
        wrap_search = lambda _s: ReplaySearch(cass)  # noqa: E731
        wrap_fetch = lambda _f: ReplayFetch(cass)  # noqa: E731
    elif args.record and not args.mock:
        wrap_search = lambda s: RecordingSearch(s, cass)  # noqa: E731
        wrap_fetch = lambda f: RecordingFetch(f, cass)  # noqa: E731
    svc = script_service(
        core=core,
        mock_or_replay=bool(args.mock or args.replay),
        provider=args.provider or provider,
        world=fake_world(provider_content_every=3) if args.mock else None,
        wrap_search=wrap_search,
        wrap_fetch=wrap_fetch,
        as_of=datetime.now(UTC).date() if not args.mock else date(2026, 11, 11),
        live_budget_micros=(spent_before + allowance_micros) if mode == "live" else None,
    )
    try:
        row = await svc.start_run("live", f"live-{name}", export)
        t0 = time.monotonic()
        while True:
            try:
                await svc.wait(row.run_id, 15)
                break
            except TimeoutError:
                snap = await svc.snapshot("live", row.run_id)
                c = snap.counters
                sys.stdout.write(
                    f"  [{name}] {int(time.monotonic() - t0)}s round {snap.round}: {c.searches_used} searches, {c.pages_read} pages, {c.facts_verified} facts, {c.targets_done}/{c.targets_total} topics\n"
                )
                sys.stdout.flush()
        research = await svc.result("live", row.run_id)
        evidence = await svc.store.get_evidence(row.run_id) or {}
        events = [
            e.model_dump_json(by_alias=True)
            for e in await svc.store.events_after(row.run_id, 0, limit=1_000_000)
        ]
    finally:
        await svc.aclose()
    metrics = run_metrics(research, evidence)
    out_root, summary_root = destinations(args)
    out = await asyncio.to_thread(
        write_artifacts, name, mode, research, evidence, events, metrics, out_root, summary_root
    )
    b = research["budget"]
    sys.stdout.write(
        f"{name}: {research['status']} items={len(research['items'])} searches={b['searches_used']} pages={b['pages_read']} {b['seconds']}s usd={b['usd']} failures={b['failures']} -> {out}\n"
    )
    return {
        "name": name,
        "usd_micros": b["usd_micros"],
        "status": research["status"],
        "verdicts": verdicts(metrics),
    }


async def main_async(args: argparse.Namespace) -> int:
    names = list(RUNS) if args.run == "all" else [args.run]
    exports: dict[str, dict[str, Any]] = {}
    for n in names:
        path = example_path(n, args.exports)
        exports[n] = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    if args.dry_run:
        total = Decimal(0)
        for n in names:
            est = estimate_run(exports[n])
            total += Decimal(est["estimate_usd"])
            sys.stdout.write(f"{n}: {json.dumps(est)}\n")
        sys.stdout.write(
            f"estimated total USD {total.quantize(Decimal('0.01'))} against an allowance of USD {args.allowance_usd}\n"
        )
        return 0
    live = not (args.mock or args.replay)
    if live:
        problems = preflight_live()
        if problems:
            sys.stderr.write("cannot run live: " + "; ".join(problems) + "\n")
            return 2
    core = load_settings()
    ledger = core.llm_budget_ledger
    spent_task = 0
    results: list[dict[str, Any]] = []
    for n in names:
        if live:
            left = usd_to_micros(args.allowance_usd) - spent_task
            if left <= usd_to_micros("0.05"):
                sys.stdout.write(
                    f"allowance nearly used (USD {micros_to_usd(spent_task)}): not starting {n} or anything after it\n"
                )
                break
            spent_before = research_spent_micros(ledger)
            allowance = left
        else:
            spent_before = allowance = 0
        r = await run_one(n, exports[n], args, spent_before, allowance)
        spent_task += r["usd_micros"]
        results.append(r)
    sys.stdout.write(
        f"done: {len(results)} run(s), USD {micros_to_usd(spent_task)} spent in this batch\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mde_research_agent.live_runs", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--run", choices=[*RUNS, "all"], default="all")
    parser.add_argument("--allowance-usd", type=Decimal, default=Decimal("4.00"))
    parser.add_argument("--provider", choices=["tavily", "serper"], default=None)
    parser.add_argument(
        "--exports",
        type=Path,
        default=None,
        help="folder with the full example exports (default docs/compass/research-inputs)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--replay", action="store_true")
    parser.add_argument(
        "--record", action="store_true", help="save search, fetch and model cassettes"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="write outputs under this folder instead of the repo"
    )
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
