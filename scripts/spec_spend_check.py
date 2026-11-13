"""Independent money check for the Spec app (doc 19 5): sums the spend record AND every Spec period ledger and exits
non-zero when either is above the cap (default USD 5.00, or SPEC_LIVE_BUDGET_USD). Read-only; prints no secrets.

    uv run python scripts/spec_spend_check.py [--data DIR] [--cap-usd 5.00]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ledger_total(path: Path) -> int:
    total = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("event") == "commit":
                cost = ev.get("cost_micros")
                if isinstance(cost, int) and cost > 0:
                    total += cost
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data",
        type=Path,
        default=Path(os.environ.get("SPEC_DATA_DIR", ROOT / "agents/spec/.runtime")),
    )
    ap.add_argument("--ledgers", type=Path, default=ROOT / "data/.budget/spec")
    ap.add_argument("--cap-usd", default=os.environ.get("SPEC_LIVE_BUDGET_USD", "5.00"))
    args = ap.parse_args(argv)
    cap = int((Decimal(str(args.cap_usd)) * 1_000_000).to_integral_value())

    record_spent = record_reserved = 0
    rec = args.data / "spend-record.json"
    if rec.is_file():
        try:
            d = json.loads(rec.read_text(encoding="utf-8"))
            record_spent = int(d.get("spent_micros", 0))
            record_reserved = sum(int(r["micros"]) for r in d.get("reservations", {}).values())
        except (ValueError, KeyError, TypeError):
            print("spend record unreadable: treated as OVER the cap", file=sys.stderr)
            return 3
    ledger = 0
    if args.ledgers.is_dir():
        for p in sorted(args.ledgers.glob("ledger-*.jsonl")):
            ledger += ledger_total(p)
    worst = max(record_spent + record_reserved, ledger)
    print(f"spend record: {record_spent} micro-USD spent, {record_reserved} reserved")
    print(f"period ledgers: {ledger} micro-USD")
    print(f"cap: {cap} micro-USD; highest reading: {worst}")
    if worst > cap:
        print("OVER THE CAP", file=sys.stderr)
        return 2
    print("within the cap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
