"""TEST-ONLY fake Spec Bench app: the real app (``create_app``) over a service whose model calls all run in the gateway's
mock mode, to drive the front end in a browser.

    uv run python agents/spec/tests/e2e/fake_app.py --port 8150

The server, the API, the run store, the budget record, the gates and the static front end (``apps/spec-bench``) are the
real ones. No model is ever called (a live request runs on the mock gateway and books no cost), no key is needed, and
everything lives in a temporary folder unless ``--data-dir`` is given. The front end shows a loud "TEST FAKE" banner
when ``GET /api/fake`` answers, which only this script adds (the real app answers 404).

Flags
    --port N            listen on 127.0.0.1:N (default 8150)
    --slow              wait between pipeline steps so a run lasts long enough to watch (--delay sets it, default 0.8 s)
    --not-ready         live runs are refused with not_ready (a list of setting NAMES is shown; offline still works)
    --total USD         the budget total (default 5.00); the tripwire is 90 percent of it
    --spent USD         book this much spend before starting (a fresh data folder only)
    --seed N            leave N finished runs in the history (approved, waiting at G2, waiting at G1.5, stopped)
    --data-dir PATH     keep everything here (a restart with the same folder finds the old runs)

This file must never be imported from ``src/`` (a test checks that the app cannot reach it).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FAKE_PORT = 8150
SEEDS = (
    ("quick-yoga-studio-bengaluru", "approve"),
    ("standard-produce-marketplace-nashik", "leave"),
    ("thorough-freight-fleet-india", "leave"),
    ("delhi-restaurant-delivery", "stop"),
    ("pune-clinic-appointments", "approve"),
)
NOT_READY_NAMES = [
    "OPENAI_API_KEY (route fast, the fake app has no key)",
    "ANTHROPIC_API_KEY (route standard)",
]


def build_service(args: argparse.Namespace, root: Path) -> Any:
    from mde_core.settings import load_settings
    from mde_spec_agent.config import load_spec_settings
    from mde_spec_agent.service import SpecService

    class FakeService(SpecService):
        """The real service with two test seams: the gateway is always the mock one, and steps may be paced."""

        def _gateway(self, offline: bool) -> Any:
            return super()._gateway(True)  # even a "live" request never leaves the machine

        async def _before_step(self, ctx: Any, name: str) -> None:
            if args.slow:
                await asyncio.sleep(float(args.delay))

    total = Decimal(str(args.total))
    settings = load_spec_settings(
        spec_data_dir=root / "data",
        spec_live_budget_usd=total,
        spec_tripwire_usd=(total * Decimal("0.9")).quantize(Decimal("0.01")),
    )
    core = load_settings(llm_mode="mock", openai_api_key=None, anthropic_api_key=None)
    service = FakeService(
        settings,
        core,
        research_runs_dir=root / "no-research",
        readiness=(lambda: list(NOT_READY_NAMES)) if args.not_ready else (lambda: []),
    )
    service.fake = True
    if float(args.spent) > 0 and service.budget()["spent_micros"] == 0:
        micros = int(Decimal(str(args.spent)) * 1_000_000)
        res = service.record.reserve(micros, label="fake: seeded spend")
        service.record.commit(res, micros)
    if args.seed:
        asyncio.run(_seed(service, int(args.seed)))
        service._sem = asyncio.Semaphore(service.settings.spec_max_concurrent_runs)
    return service


async def _seed(service: Any, n: int) -> None:
    for compass_id, action in SEEDS[:n]:
        started = await service.start_run(compass_id=compass_id, offline=True)
        task = service._tasks.get(started.run_id)
        if task is not None:
            await asyncio.shield(task)
        rec = service.store.get_run(started.run_id)
        if action == "approve" and rec["status"] == "awaiting_g2":
            service.approve_g2(started.run_id, str(rec["content_hash"]))
        elif action == "stop" and rec["status"] in (
            "paused_g15",
            "awaiting_g2",
            "queued",
            "running",
        ):
            await service.stop_run(
                started.run_id
            )  # (an input whose plan fails ends partial by itself)


def build_app(args: argparse.Namespace, root: Path) -> Any:
    from fastapi.routing import APIRoute

    from mde_spec_agent.api import create_app

    service = build_service(args, root)
    app = create_app(service, settings=service.settings)

    async def fake() -> dict[str, Any]:
        return {
            "fake": True,
            "slow": bool(args.slow),
            "not_ready": bool(args.not_ready),
            "total_usd": str(args.total),
            "spent_usd": str(args.spent),
            "seed": int(args.seed),
            "note": "TEST FAKE: the real app over the gateway's mock mode. No model is called.",
        }

    app.add_api_route("/api/fake", fake, methods=["GET"])
    route = app.router.routes.pop()
    assert isinstance(route, APIRoute)
    app.router.routes.insert(0, route)  # before the static mount at "/"
    return app


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TEST-ONLY fake Spec Bench app")
    p.add_argument("--port", type=int, default=FAKE_PORT)
    p.add_argument("--slow", action="store_true")
    p.add_argument("--delay", type=float, default=0.8)
    p.add_argument("--not-ready", dest="not_ready", action="store_true")
    p.add_argument("--total", default="5.00")
    p.add_argument("--spent", default="0")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--data-dir", dest="data_dir", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.environ.setdefault("MDE_ENV_FILE", "")  # no dotenv file is read
    root = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="spec-fake-"))
    root.mkdir(parents=True, exist_ok=True)
    app = build_app(args, root)
    import uvicorn

    sys.stdout.write(f"TEST FAKE Spec Bench: http://127.0.0.1:{args.port}/  (data: {root})\n")
    sys.stdout.flush()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
