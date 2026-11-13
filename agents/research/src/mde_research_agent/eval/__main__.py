"""``python -m mde_research_agent.eval planted|probe``: the evaluation harness for the owner to run later.

    planted --run RUN_ID [--seed N]     plant errors in a recorded run's items and measure what the checks catch
    reextract --run-dir DIR [--arms A,A3,B] [--repeats 3] [--max-pairs N]
                                        re-run extraction and checking on a recorded run's stored pages (model calls
                                        only; no search, no fetch) and compare checker arms by ranges
    probe   --run RUN_ID                stand-in Spec with and without the research, judged; it is given the run's real
                                        Brief (from the run's stored input, or --input FILE with a ResearchInput json)

Both need a COMMITTED run in the app's data folder (``--run``) or ``--research FILE --evidence FILE``. ``--dry-run`` prints the
estimate and spends nothing; ``--mock`` runs offline over doubles; without either flag the commands are LIVE: they refuse
unless ``LLM_MODE=live`` is already set, print their estimate, book every cent in the spend record (kind ``probe``) and
stop before the budget tripwire. Stop the app first: the data folder has one owner at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mde_core.money import micros_to_usd
from mde_research_agent.eval import planted as planted_mod
from mde_research_agent.eval import probe as probe_mod
from mde_research_agent.eval import reextract as reextract_mod


def _load_files(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(Path(args.research).read_text(encoding="utf-8")),
        json.loads(Path(args.evidence).read_text(encoding="utf-8")),
    )


def _load_run_dir(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """The stored evidence and the research input of a run folder (``.runtime/runs/<id>``)."""
    import gzip

    root = Path(args.run_dir)
    with gzip.open(root / "evidence.json.gz", "rt", encoding="utf-8") as fh:
        evidence = json.load(fh)
    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
    return evidence, dict(run["input_json"])


def _load_brief(args: argparse.Namespace) -> str:
    if not args.input:
        return ""
    return probe_mod.brief_from_input(json.loads(Path(args.input).read_text(encoding="utf-8")))


async def _run(args: argparse.Namespace) -> int:
    if args.dry_run:
        est = (
            planted_mod.estimate()
            if args.what == "planted"
            else {
                "repeats": args.repeats,
                "arms": args.arms,
                "usd_estimate": "0.02 to 0.04 per repeat on a Small run",
            }
            if args.what == "reextract"
            else {
                "tasks": len(probe_mod.tasks()),
                "model_calls": len(probe_mod.tasks()) * 4,
                "usd_estimate": "0.05 to 0.20",
            }
        )
        sys.stdout.write(
            json.dumps({"command": args.what, "estimate": est, "spends": False}, indent=1) + "\n"
        )
        return 0
    from mde_core.settings import load_settings
    from mde_research_agent.calls.base import CallRunner
    from mde_research_agent.devtools import script_service
    from mde_research_agent.run import preflight_live

    brief = _load_brief(args)
    if args.mock:
        svc = script_service(
            core=load_settings(llm_mode="mock"), mock_or_replay=True, provider="fake"
        )
        research, evidence = ({}, {}) if args.what == "reextract" else _load_files(args)
        scope: tuple[str, ...] = ()
        record = None
    else:
        problems = preflight_live()
        if problems:
            sys.stderr.write("cannot run live: " + "; ".join(problems) + "\n")
            return 2
        from mde_research_agent.compose import compose_app

        svc = compose_app()
        if svc.control is None or svc.record is None:
            sys.stderr.write("the spend record is not available\n")
            return 2
        ok, why = svc.control.can_start()
        if not ok:
            sys.stderr.write(f"not started: {why}\n")
            return 2
        if args.what == "reextract":
            research, evidence = {}, {}
        elif args.run:
            res = await svc.store.get_result(args.run)
            ev = await svc.store.get_evidence(args.run)
            if res is None or ev is None:
                sys.stderr.write("that run is not committed in the data folder\n")
                return 2
            research, evidence = res, ev
            row = await svc.store.get_run(args.tenant, args.run)
            if row is not None and row.input_json and not brief:
                brief = probe_mod.brief_from_input(dict(row.input_json))
        else:
            research, evidence = _load_files(args)
        scope = (svc.control.period_scope,)
        record = svc.record
    runner = CallRunner(svc.gateway, run_id="eval", ids=svc.ids, extra_scopes=scope)
    entry = record.start_entry("probe", label=f"eval {args.what}") if record else None
    try:
        if args.what == "reextract":
            run_evidence, run_input = _load_run_dir(args)
            report = await reextract_mod.run_reextract(
                runner,
                run_evidence,
                run_input,
                arms=[x.strip() for x in args.arms.split(",") if x.strip()],
                repeats=args.repeats,
                max_pairs=args.max_pairs,
            )
        elif args.what == "planted":
            report = await planted_mod.run_planted(
                runner, research, evidence, seed=args.seed, checker=args.checker
            )
        else:
            report = await probe_mod.run_probe(runner, research, brief=brief)
    finally:
        cost = sum(r.cost_micros for _, r in runner.calls)
        if record and entry:
            record.add_cost(entry, "probe", cost)
            record.finish_entry(entry, "done")
        await svc.aclose()
    report["cost_usd"] = micros_to_usd(report.get("cost_micros", cost))
    sys.stdout.write(json.dumps(report, indent=1, default=str) + "\n")
    return 0 if report.get("passed", True) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mde_research_agent.eval", description=__doc__.splitlines()[0])  # type: ignore[union-attr]
    p.add_argument("what", choices=["planted", "probe", "reextract"])
    p.add_argument("--run", default=None)
    p.add_argument("--research", default=None)
    p.add_argument("--evidence", default=None)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument(
        "--checker", choices=["v2", "v3"], default="v3", help="planted: which checker to test"
    )
    p.add_argument("--run-dir", default=None, help="reextract: a run folder under .runtime/runs")
    p.add_argument("--arms", default="A,A3,B", help="reextract: checker arms to compare")
    p.add_argument(
        "--repeats", type=int, default=3, help="reextract: repeats per arm (ranges, not one run)"
    )
    p.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="reextract: use only this many (topic, page) pairs, evenly spread (a cheaper sample)",
    )
    p.add_argument(
        "--tenant", default="demo", help="the tenant the run was made under (the app's default)"
    )
    p.add_argument(
        "--input",
        default=None,
        help="probe: a ResearchInput json (a run's input_json) to use as the Brief",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
