"""``python -m mde_llm_gateway.budget_cli [--scope S] [--json]``: live spend from the ledger.

Prints total spent, percent of the cap, spend by scope and the live-call count. Read-only: it never
takes the ledger lock, never creates files and never prints a key. ``just budget`` calls this.

``python -m mde_llm_gateway.budget_cli init [--force]`` creates the ledger and its guard file (a new
ledger needs no ``init``: the first reservation creates both). When the guard says the ledger once
held more than it does now (deleted, truncated), ``init`` refuses; ``init --force`` is the operator's
explicit reset: the ledger as it is now becomes the truth. Reconcile with the provider dashboard
first: a reset can hand out budget that was already spent.

A relative ledger path (``LLM_BUDGET_LEDGER``) is anchored to the MAIN repository root, so every
worktree and working directory reads the same ledger.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast

from mde_core.money import micros_to_usd
from mde_core.paths import anchor_to_main_repo
from mde_core.settings import load_settings
from mde_llm_gateway.budget import BudgetConfig
from mde_llm_gateway.ledger import FileLedger, LedgerSnapshot


def _usd(micros: int) -> str:
    return f"{micros_to_usd(micros):.4f}"


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.0"
    return f"{Decimal(part) * 100 / Decimal(whole):.1f}"


def build_report(
    snapshot: LedgerSnapshot, config: BudgetConfig, *, scope: str | None = None
) -> dict[str, object]:
    """The report as plain data (also the ``--json`` output). Money as USD strings plus micros."""
    scopes = {
        name: micros
        for name, micros in sorted(snapshot.spent_by_scope.items())
        if scope is None or name == scope or name.startswith(scope)
    }
    committed = snapshot.spent_micros + snapshot.reserved_micros
    return {
        "cap_usd": _usd(config.cap_micros),
        "effective_cap_usd": _usd(config.effective_cap_micros),
        "spent_micros": snapshot.spent_micros,
        "spent_usd": _usd(snapshot.spent_micros),
        "reserved_micros": snapshot.reserved_micros,
        "reserved_usd": _usd(snapshot.reserved_micros),
        "pct_of_cap": _pct(snapshot.spent_micros, config.cap_micros),
        "pct_of_effective_cap": _pct(snapshot.spent_micros, config.effective_cap_micros),
        "tripwire": config.tripwire(committed),
        "live_calls": snapshot.live_calls,
        "open_reservations": snapshot.open_reservations,
        "stale_reservations": snapshot.stale_reservations,
        "stale_usd": _usd(snapshot.stale_micros),
        "corrupt_lines": snapshot.corrupt_lines,
        "scope_filter": scope,
        "by_scope": {
            name: {
                "spent_micros": micros,
                "spent_usd": _usd(micros),
                "cap_usd": _usd(cap) if (cap := config.scope_cap(name)) is not None else None,
            }
            for name, micros in scopes.items()
        },
    }


def render_text(report: dict[str, object]) -> str:
    lines = [
        f"live spend      : USD {report['spent_usd']}  ({report['pct_of_cap']}% of cap, "
        f"{report['pct_of_effective_cap']}% of effective cap)",
        f"cap / effective : USD {report['cap_usd']} / USD {report['effective_cap_usd']}",
        f"reserved (open) : USD {report['reserved_usd']}  ({report['open_reservations']} open)",
        f"tripwire        : {report['tripwire']}",
        f"live calls      : {report['live_calls']}",
    ]
    if report["stale_reservations"]:
        lines.append(
            f"stale (spent)   : {report['stale_reservations']} reservation(s) never closed, "
            f"counted as spent at their worst case: USD {report['stale_usd']}"
        )
    if report["corrupt_lines"]:
        lines.append(f"WARNING         : {report['corrupt_lines']} unreadable ledger line(s)")
    if report.get("integrity_problem"):
        lines.append(f"WARNING         : {report['integrity_problem']}")
    by_scope = cast("dict[str, dict[str, object]]", report["by_scope"])
    label = f" (filter: {report['scope_filter']})" if report["scope_filter"] else ""
    lines.append(f"by scope{label}:")
    if not by_scope:
        lines.append("  (none)")
    for name, row in by_scope.items():
        cap = f" / cap USD {row['cap_usd']}" if row["cap_usd"] else ""
        lines.append(f"  {name:<40} USD {row['spent_usd']}{cap}")
    return "\n".join(lines)


def _resolve(arg: Path | None) -> Path:
    """``--ledger`` is relative to the working directory (the operator typed it); the default is
    ``LLM_BUDGET_LEDGER`` anchored to the main repository."""
    if arg is not None:
        return arg.expanduser().resolve()
    return anchor_to_main_repo(load_settings().llm_budget_ledger)


def _init(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mde_llm_gateway.budget_cli init",
        description="Create the ledger and its guard file; --force resets a guard on purpose.",
    )
    parser.add_argument("--ledger", type=Path, help="ledger path (default: LLM_BUDGET_LEDGER)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="accept the ledger as it is now even if the guard says it was deleted or truncated",
    )
    args = parser.parse_args(argv)
    ledger = FileLedger(_resolve(args.ledger))
    problem = ledger.integrity_problem()
    if problem is not None and not args.force:
        sys.stderr.write(
            f"refused: {problem}\n"
            "If you have reconciled the spend with the provider dashboard, run again with --force.\n"
        )
        return 2
    if args.force:
        ledger.reset_guard()
        sys.stdout.write(f"guard reset on the ledger as it is now: {ledger.path}\n")
    if not ledger.path.exists():
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        ledger.path.touch()
    if ledger.read_guard() is None:
        ledger.reset_guard()
        sys.stdout.write(f"initialised {ledger.path} and its guard\n")
    elif not args.force:
        sys.stdout.write(f"ledger already initialised: {ledger.path}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] == "init":
        return _init(args_in[1:])
    parser = argparse.ArgumentParser(
        prog="python -m mde_llm_gateway.budget_cli", description="Show live LLM spend (ledger)."
    )
    parser.add_argument("--scope", help="only scopes equal to or starting with this value")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--ledger", type=Path, help="ledger path (default: LLM_BUDGET_LEDGER)")
    args = parser.parse_args(args_in)

    settings = load_settings()
    config = BudgetConfig.load(cap_usd=settings.llm_budget_usd)
    ledger = FileLedger(_resolve(args.ledger))
    report = build_report(ledger.peek(), config, scope=args.scope)
    report["integrity_problem"] = ledger.integrity_problem()
    sys.stdout.write((json.dumps(report, indent=2) if args.json else render_text(report)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
