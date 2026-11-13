"""`python -m mde_spec_core check <pack-dir>`: lint a pack folder and, when a graph.json is beside it, run `spec check`.

Also `check --synth <preset>` (assemble a synthetic preset to a temp folder and check that). Findings are printed in
plain words; the exit code is 1 when anything blocks, 2 on a usage error. Anyone can run it; it costs nothing.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .bn import plain_lines
from .check import spec_check
from .contract import build_contract
from .errors_out import counts, plain_words, sort_findings
from .graph import SpecGraph
from .lint_pack import lint_pack_dir, pack_from_files, read_pack_dir, write_pack_dir
from .validators.base import Finding, blockers

GRAPH_FILE = "graph.json"


def _find_graph(pack_dir: Path) -> Path | None:
    for cand in (pack_dir / GRAPH_FILE, pack_dir.parent / GRAPH_FILE):
        if cand.is_file():
            return cand
    return None


_SHOW = 12  # lines per section; blockers always come first, the rest is counted


def _print(findings: list[Finding], out) -> None:  # type: ignore[no-untyped-def]
    rows = [f for f in sort_findings(findings) if f.severity != "info"]
    for f in rows[:_SHOW]:
        print(plain_words(f), file=out)
    if len(rows) > _SHOW:
        print(f"... and {len(rows) - _SHOW} more (all blockers are listed first)", file=out)


def run_check(pack_dir: Path, graph: SpecGraph | None, out=None) -> int:  # type: ignore[no-untyped-def]
    """Check one pack folder (and its graph when given). Returns the exit code."""
    out = out or sys.stdout
    findings = lint_pack_dir(pack_dir, graph=graph)
    print(f"Pack linter on {pack_dir}: {len(findings)} findings", file=out)
    _print(findings, out)
    bad = bool(blockers(findings))
    if graph is not None:
        files = read_pack_dir(pack_dir)
        report = spec_check(graph, pack_from_files(files))
        print(
            f"spec check: {'OK' if report.ok else 'NOT OK'}: "
            f"{report.by_severity['blocker']} blockers, {report.by_severity['warning']} warnings",
            file=out,
        )
        notes = plain_lines(report.needs)
        print(f"Builder needs: {len(notes)} of 17 are not plainly present", file=out)
        for line in notes:
            print(f"  {line}", file=out)
        _print(report.findings, out)
        for n in report.missing:
            print(f"[BLOCKER] {n.id} {n.title}: missing: {n.why}", file=out)
        bad = bad or not report.ok
        contract = build_contract(graph)
        cf = [f for f in contract.findings if f.severity != "info"]
        print(
            f"Contract v0: {len(contract.operations)} operations, "
            f"{'split into files' if contract.split else 'one document'}, {len(cf)} findings",
            file=out,
        )
        _print(cf, out)
        for w in contract.warnings:
            print(f"[warning] contract: {w}", file=out)
        bad = bad or bool(blockers(cf))
    c = counts(findings)["by_severity"]
    print(
        "RESULT: "
        + ("BLOCKED" if bad else "OK")
        + f" ({c['blocker']} blockers, {c['warning']} warnings in the pack)",
        file=out,
    )
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mde_spec_core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser(
        "check", help="lint a pack folder (G-1..G-8) and run spec check when a graph is beside it"
    )
    ck.add_argument("pack_dir", nargs="?", help="the pack folder")
    ck.add_argument(
        "--synth",
        metavar="PRESET",
        help="check a synthetic preset (tiny small medium big program erp)",
    )
    ck.add_argument("--graph", help="a graph.json to check the pack against")
    args = ap.parse_args(argv)

    if args.synth:
        from .pack import assemble
        from .synth import PRESETS, make_graph

        if args.synth not in PRESETS:
            sys.stderr.write(
                f"unknown preset {args.synth!r}; choose from {', '.join(sorted(PRESETS))}\n"
            )
            return 2
        g = make_graph(args.synth)
        with tempfile.TemporaryDirectory() as tmp:
            write_pack_dir(assemble(g), tmp)
            return run_check(Path(tmp), g)
    if not args.pack_dir:
        sys.stderr.write("give a pack folder or --synth PRESET\n")
        return 2
    root = Path(args.pack_dir)
    if not root.is_dir():
        sys.stderr.write(f"{root} is not a folder\n")
        return 2
    gpath = Path(args.graph) if args.graph else _find_graph(root)
    graph = SpecGraph.model_validate_json(gpath.read_text(encoding="utf-8")) if gpath else None
    return run_check(root, graph)


if __name__ == "__main__":
    raise SystemExit(main())
