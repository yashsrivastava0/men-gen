"""``python -m mde_spec_agent``: the Spec Bench from a terminal (doc 19 4.3, docs/api/spec-agent.md "Headless").

    bench auto [--size compact|standard|deep|program] [--json]   print the proposed Compass + Research pairs
    bench run --input <path|id|synth:name> [--research <path>] [--no-research] [--offline]
    serve [--host 127.0.0.1] [--port 8130]                       the Spec Bench app (loopback only)

Exit codes: 0 done, 1 the run itself failed, 2 bad input or arguments, 3 a part that is built later is not there yet.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from mde_spec_agent.bench.autoselect import SIZES, Proposal, auto_select, best_research
from mde_spec_agent.bench.inputs import (
    CompassDiscovery,
    CompassEntry,
    ResearchEntry,
    classify_file,
    default_dirs,
    default_research_runs_dir,
    discover_compass_report,
    discover_research,
    entry_for_path,
    resolve_compass,
    synth_name,
)
from mde_spec_agent.config import DEFAULT_PORT
from mde_spec_core.synth import PRESETS

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_NOT_BUILT = 0, 1, 2, 3
LOOPBACK = ("127.0.0.1", "localhost", "::1")
NOT_BUILT_RUN = "the run service is not built yet"
NOT_BUILT_SERVE = "the Spec Bench app is not built yet"
_SERVICE_MODULES = {"mde_spec_agent.service"}
_API_MODULES = {"mde_spec_agent.api", "mde_spec_agent.api.main"}


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


# --- bench auto ------------------------------------------------------------------------------------------------------


def _discover() -> tuple[CompassDiscovery, list[ResearchEntry]]:
    return discover_compass_report(default_dirs()), discover_research(default_research_runs_dir())


def _format_proposal(p: Proposal) -> str:
    lines = [
        f"[{p.size}] {p.label}",
        f"  compass:  {p.compass_id}" + ("  (synthetic)" if p.synthetic else ""),
    ]
    lines.append(f"  research: {p.research_id or 'none'}")
    lines.append(f"  profile:  {p.profile}")
    lines.append(f"  why:      {p.reason}")
    lines.extend(f"  gap:      {g}" for g in p.gaps)
    return "\n".join(lines)


def _cmd_auto(args: argparse.Namespace) -> int:
    found, research = _discover()
    proposals = auto_select(list(found.entries), research, args.size)
    if args.json:
        payload = {
            "proposals": [p.model_dump(mode="json") for p in proposals],
            "compass_found": len(found.entries),
            "research_found": len(research),
            "skipped": [s.model_dump(mode="json") for s in found.skipped],
        }
        _out(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    _out(f"Compass exports found: {len(found.entries)}; Research runs found: {len(research)}")
    _out()
    for p in proposals:
        _out(_format_proposal(p))
        _out()
    if not proposals:
        _out("nothing to propose for that size")
    for s in found.skipped:
        _out(f"skipped {s.path}: {s.reason}")
    return EXIT_OK


# --- bench run -------------------------------------------------------------------------------------------------------


def _resolve_research(
    args: argparse.Namespace, entry: CompassEntry | None, research: list[ResearchEntry]
) -> tuple[Path | None, str] | int:
    if args.research:
        path = Path(args.research).expanduser()
        if path.is_dir():
            path = path / "research.json"
        if not path.is_file():
            _err(f"research file not found: {args.research}")
            return EXIT_USAGE
        return path, f"research: {path} (given)"
    if args.no_research or entry is None:
        return (
            None,
            "research: none (Compass only: Spec runs with a thin research lane and says so)",
        )
    run, gaps = best_research(entry, research)
    if run is None:
        return None, f"research: none ({gaps[0]})"
    note = f"research: {run.id} ({run.status})"
    return Path(run.research_path), note + (f"; {gaps[0]}" if gaps else "")


def _load_service() -> Any | None:
    try:
        import importlib

        return importlib.import_module("mde_spec_agent.service")
    except ModuleNotFoundError as exc:
        if exc.name in _SERVICE_MODULES:
            return None
        _err(f"{NOT_BUILT_RUN}: it could not be imported ({exc})")
        raise _NotBuilt from exc
    except ImportError as exc:
        _err(f"{NOT_BUILT_RUN}: it could not be imported ({exc})")
        raise _NotBuilt from exc


class _NotBuilt(Exception):
    pass


def _print_summary(result: object) -> None:
    def get(name: str) -> object:
        if isinstance(result, dict):
            return result.get(name)
        return getattr(result, name, None)

    shown = False
    for name, label in (
        ("run_dir", "run folder"),
        ("run_id", "run id"),
        ("status", "status"),
        ("gates", "gates"),
        ("pack_hash", "pack hash"),
    ):
        value = get(name)
        if value is not None:
            _out(f"{label}: {value}")
            shown = True
    if not shown:
        _out(str(result))


def _cmd_run(args: argparse.Namespace) -> int:
    found, research = _discover()
    entries = list(found.entries)
    target = resolve_compass(args.input, entries)
    synth: str | None = None
    compass_path: Path | None = None
    entry: CompassEntry | None = None
    if target is None:
        _err(f"unknown input: {args.input} (an id from `bench auto`, a file path, or synth:<name>)")
        return EXIT_USAGE
    if isinstance(target, str):
        synth = synth_name(target)
        if synth not in PRESETS:
            _err(f"unknown synthetic input: {synth} (one of {', '.join(sorted(PRESETS))})")
            return EXIT_USAGE
    elif isinstance(target, CompassEntry):
        entry, compass_path = target, Path(target.path)
    else:
        compass_path = target
        entry = entry_for_path(target, entries)
        if entry is None:
            entry, reason = classify_file(target)
            if entry is None:
                _err(f"{target} cannot be used: {reason}")
                return EXIT_USAGE
    if entry is not None and not entry.readiness.ready:
        _err(
            f"{entry.id} is not ready: Compass must still ask: "
            + "; ".join(entry.readiness.missing)
        )
        return EXIT_USAGE
    if synth is not None and args.research:
        _err("a synthetic input has no Research run to pair with")
        return EXIT_USAGE
    resolved = (
        (None, "research: none (a synthetic input)")
        if synth is not None
        else _resolve_research(args, entry, research)
    )
    if isinstance(resolved, int):
        return resolved
    research_path, research_note = resolved
    try:
        service = _load_service()
    except _NotBuilt:
        return EXIT_NOT_BUILT
    if service is None or not hasattr(service, "run_to_end"):
        _err(NOT_BUILT_RUN)
        return EXIT_NOT_BUILT
    _out(f"input: {('synth:' + synth) if synth else compass_path}")
    _out(research_note)
    _out(f"mode: {'offline' if args.offline else 'live (inside the budget cap)'}")
    try:
        result = service.run_to_end(
            compass_path=compass_path,
            synth=synth,
            research_path=research_path,
            offline=bool(args.offline),
        )
        if inspect.isawaitable(result):
            result = asyncio.run(_await(result))
    except Exception as exc:  # the owner gets a plain line, not a traceback
        _err(f"the run failed: {exc.__class__.__name__}: {exc}")
        return EXIT_FAILED
    _print_summary(result)
    return EXIT_OK


async def _await(awaitable: Any) -> Any:
    return await awaitable


# --- serve -----------------------------------------------------------------------------------------------------------


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.host not in LOOPBACK:
        _err("refusing a non-loopback host: dev authentication must never face a network")
        return EXIT_USAGE
    try:
        import importlib

        module = importlib.import_module("mde_spec_agent.api.main")
    except ModuleNotFoundError as exc:
        if exc.name in _API_MODULES:
            _err(NOT_BUILT_SERVE)
            return EXIT_NOT_BUILT
        raise
    code = module.main(["--host", args.host, "--port", str(args.port)])
    return int(code or 0)


# --- parser ----------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mde_spec_agent", description="The Spec agent and its bench"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bench = sub.add_parser("bench", help="pick inputs and run Spec end to end from a terminal")
    bsub = bench.add_subparsers(dest="bench_command", required=True)

    auto = bsub.add_parser("auto", help="print the proposed Compass + Research pairs")
    auto.add_argument("--size", choices=SIZES, default=None)
    auto.add_argument("--json", action="store_true", help="machine-readable output")
    auto.set_defaults(func=_cmd_auto)

    run = bsub.add_parser("run", help="run Spec on one input, end to end")
    run.add_argument(
        "--input", required=True, help="a file path, an id from `bench auto`, or synth:<name>"
    )
    run.add_argument(
        "--research",
        default=None,
        help="a research.json (or its run folder); default: the best match",
    )
    run.add_argument(
        "--no-research", action="store_true", help="Compass only: a thin research lane"
    )
    run.add_argument(
        "--offline", action="store_true", help="no model call: deterministic stand-ins"
    )
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="the Spec Bench app (loopback only)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
