"""Headless run: ``python -m mde_research_agent.run --export <path> [--mock | --live] [--out DIR]``.

Reads a Compass format-2 export, runs Research in process (in-memory store) and writes ``research.json``,
``research_evidence.json`` and ``events.jsonl`` into ``--out``. ``--mock`` forces mock models and fake search and
fetch (no network, no key). ``--live`` refuses to start unless the owner's live switches (the model mode and the
second key) are ALREADY in the process environment (this command never sets them) and a search key is present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from mde_core.settings import load_settings
from mde_research_agent.compose import build_service
from mde_research_agent.config import keys_present, load_research_settings
from mde_research_agent.fakes import FakeWorld
from mde_research_agent.intake import ExportRefused


def preflight_live(environ: dict[str, str] | None = None) -> list[str]:
    """Plain-word reasons a live run cannot start (empty means it may). Reads the environment, never writes it."""
    env = dict(os.environ) if environ is None else environ
    problems: list[str] = []
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


async def run_export(
    export: dict[str, Any],
    *,
    mock: bool,
    program_id: str | None,
    as_of: date | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Run once; return ``(research.json, research_evidence, event lines)``. Files are written by the caller."""
    core = load_settings(llm_mode="mock") if mock else load_settings()
    svc = build_service(
        memory=True,
        core_settings=core,
        as_of=as_of,
        world=FakeWorld(provider_content_every=3) if mock else None,
        provider="fake" if mock else None,
    )
    try:
        row = await svc.start_run("cli", program_id, export)
        await svc.wait(row.run_id, 3600)
        res = await svc.result("cli", row.run_id)
        evidence = await svc.store.get_evidence(row.run_id) or {}
        events = await svc.store.events_after(row.run_id, 0, limit=1_000_000)
        return res, evidence, [e.model_dump_json(by_alias=True) for e in events]
    finally:
        await svc.aclose()


def write_outputs(
    out: Path, res: dict[str, Any], evidence: dict[str, Any], events: list[str]
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "research.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "research_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "events.jsonl").write_text("".join(line + "\n" for line in events), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mde_research_agent.run", description="Run Research headless on a Compass export"
    )
    parser.add_argument("--export", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock", action="store_true", help="mock models, fake search and fetch (offline)"
    )
    mode.add_argument(
        "--live", action="store_true", help="real models and search (the owner's switches)"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--program-id", default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    if args.live:
        problems = preflight_live()
        if problems:
            sys.stderr.write("cannot run live: " + "; ".join(problems) + "\n")
            return 2
    try:
        export = json.loads(args.export.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"cannot read the export: {exc}\n")
        return 2
    try:
        res, evidence, events = asyncio.run(
            run_export(export, mock=args.mock, program_id=args.program_id, as_of=args.as_of)
        )
        if args.out is not None:
            write_outputs(args.out, res, evidence, events)
    except ExportRefused as exc:
        sys.stderr.write(f"refused: {exc.message} ({exc.code})\n")
        return 2
    b = res["budget"]
    sys.stdout.write(
        f"status={res['status']} items={len(res['items'])} norms={len(res['norms'])} "
        f"searches={b['searches_used']} pages={b['pages_read']} failures={b['failures']} usd={b['usd']}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
