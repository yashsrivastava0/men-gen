"""``python -m mde_research_agent.eval planted|probe``: the evaluation harness for the owner to run later.

    planted --run RUN_ID [--seed N]     plant errors in a recorded run's items and measure what the checks catch
    probe   --run RUN_ID                stand-in Spec with and without the research, judged

Both need a COMMITTED run in the app's data folder (``--run``) or ``--research FILE --evidence FILE``. ``--dry-run`` prints the
estimate and spends nothing; ``--mock`` runs offline over doubles; without either flag the commands are LIVE: they refuse
unless the owner's switches are already set, print their estimate, book every cent in the spend record (kind ``probe``) and
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


def _load_files(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(Path(args.research).read_text(encoding="utf-8")),
        json.loads(Path(args.evidence).read_text(encoding="utf-8")),
    )


async def _run(args: argparse.Namespace) -> int:
    if args.dry_run:
        est = (
            planted_mod.estimate()
            if args.what == "planted"
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

    if args.mock:
        svc = script_service(
            core=load_settings(llm_mode="mock"), mock_or_replay=True, provider="fake"
        )
        research, evidence = _load_files(args)
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
        if args.run:
            res = await svc.store.get_result(args.run)
            ev = await svc.store.get_evidence(args.run)
            if res is None or ev is None:
                sys.stderr.write("that run is not committed in the data folder\n")
                return 2
            research, evidence = res, ev
        else:
            research, evidence = _load_files(args)
        scope = (svc.control.period_scope,)
        record = svc.record
    runner = CallRunner(svc.gateway, run_id="eval", ids=svc.ids, extra_scopes=scope)
    entry = record.start_entry("probe", label=f"eval {args.what}") if record else None
    try:
        report = (
            await planted_mod.run_planted(runner, research, evidence, seed=args.seed)
            if args.what == "planted"
            else await probe_mod.run_probe(runner, research)
        )
    finally:
        cost = sum(r.cost_micros for _, r in runner.calls)
        if record and entry:
            record.add_cost(entry, "probe", cost)
            record.finish_entry(entry, "done")
        await svc.aclose()
    report["cost_usd"] = micros_to_usd(report.get("cost_micros", cost))
    sys.stdout.write(json.dumps(report, indent=1) + "\n")
    return 0 if report.get("passed") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mde_research_agent.eval", description=__doc__.splitlines()[0])  # type: ignore[union-attr]
    p.add_argument("what", choices=["planted", "probe"])
    p.add_argument("--run", default=None)
    p.add_argument("--research", default=None)
    p.add_argument("--evidence", default=None)
    p.add_argument("--seed", type=int, default=1)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
