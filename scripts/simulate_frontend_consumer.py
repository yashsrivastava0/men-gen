"""Consumer simulator (brief 8.5): walks a spec file's build sequence, calls ``slice()`` for every
step and screen, prints token sizes, and fails if a slice exceeds the budget (default 10k
estimated tokens), a reference does not resolve, a screen is built by no step (or twice), or the
embedded hash does not verify. A contract test, not a code generator.

Usage: python scripts/simulate_frontend_consumer.py <APP_SPEC.md> [--budget 10000]
Exit code: 0 ok, 1 contract violation, 2 the file cannot be read or parsed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mde_spec_loader import DEFAULT_BUDGET_TOKENS, SpecError, SpecIndex, simulate_consumer


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Simulate how the frontend engine reads a spec.")
    parser.add_argument("spec", type=Path, help="path to APP_SPEC.md")
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET_TOKENS,
        help=f"max estimated tokens per slice (default {DEFAULT_BUDGET_TOKENS})",
    )
    args = parser.parse_args(argv[1:])

    try:
        index = SpecIndex.from_path(args.spec)
        report = simulate_consumer(index, budget_tokens=args.budget)
        view = index.index()
    except (OSError, UnicodeDecodeError, SpecError) as exc:
        print(f"cannot read {args.spec}: {exc}", file=sys.stderr)
        return 2

    print(
        f"{args.spec.name}: version {view.spec_version}, "
        f"{sum(1 for b in view.blocks if b.kind == 'screen')} screens, "
        f"~{view.total_tokens} tokens in total, hash {'ok' if report.hash_ok else 'MISMATCH'}"
    )
    print(f"budget per slice: {report.budget_tokens} tokens")
    print(f"{'target':<10} {'kind':<7} {'tokens':>7}")
    for item in report.slices:
        flag = "  OVER BUDGET" if item.over_budget else ""
        print(f"{item.id:<10} {item.kind:<7} {item.tokens:>7}{flag}")
    print(f"largest slice: {report.max_tokens} tokens")

    if report.problems:
        print(f"\n{len(report.problems)} problem(s):", file=sys.stderr)
        for problem in report.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("ok: every slice is within budget and every reference resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
