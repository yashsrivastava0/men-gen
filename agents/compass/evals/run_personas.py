"""Run the ten persona scripts against the real Compass service (mock mode, in-memory store) and print a JSON report.

    cd agents/compass && PYTHONPATH=src python -m evals.run_personas [--only silent,chatty] [--no-write]

Offline and free: every call runs its mock handler (``LLM_MODE=mock`` is forced), on a fake clock. The report is
written to ``agents/compass/evals/reports/`` (git-ignored) as ``personas-<UTC time>.json`` and ``latest.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

PERSONA_DIR = Path(__file__).resolve().parent / "personas"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


NEEDLE_SCENARIO = (
    Path(__file__).resolve().parents[1] / "tests" / "scenarios" / "data" / "SB-QP-20.yaml"
)


async def _needle() -> dict | None:
    """The needle test: a fact stated in turn 2 must reach the final Brief after 25 turns (scenario SB-QP-20)."""
    from mde_compass.scenarios import load_scenario, run_scenario

    if not NEEDLE_SCENARIO.exists():
        return None
    result = await run_scenario(load_scenario(NEEDLE_SCENARIO), engine="planner")
    return {"scenario": "SB-QP-20", "present": result.ok, "failure": result.failure}


def _quiet() -> None:
    logging.disable(logging.CRITICAL)
    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))


async def run(only: list[str] | None = None, engine: str = "legacy") -> dict:
    from mde_compass.scenarios import load_dir, run_scenario
    from mde_compass.scenarios.metrics import aggregate, collect

    extras: dict = {}
    if engine == "planner" and not only:
        from mde_compass.scenarios.followups import measure_followups

        extras["followups"] = await measure_followups(engine)
        extras["needle"] = await _needle()
    outcomes = []
    for _path, scn in load_dir(PERSONA_DIR):
        key = scn.id.removeprefix("PERSONA-")
        if only and key not in only:
            continue
        result = await run_scenario(scn, engine=engine)  # type: ignore[arg-type]
        outcomes.append(await collect(result))
    return aggregate(outcomes, engine=engine, extras=extras)


def main(argv: list[str] | None = None) -> int:
    os.environ["LLM_MODE"] = "mock"  # never live from here
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="comma separated persona names")
    parser.add_argument("--no-write", action="store_true", help="print only, write no report file")
    parser.add_argument(
        "--engine",
        choices=("legacy", "planner"),
        default="legacy",
        help="the question engine to run (default legacy until the lead flips it)",
    )
    args = parser.parse_args(argv)
    _quiet()
    from mde_compass.scenarios.metrics import dumps

    report = asyncio.run(run([s for s in args.only.split(",") if s] or None, args.engine))
    report["generated_at"] = datetime.now(UTC).isoformat()
    text = dumps(report)
    if not args.no_write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (REPORT_DIR / f"personas-{stamp}.json").write_text(text, encoding="utf-8")
        (REPORT_DIR / "latest.json").write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
