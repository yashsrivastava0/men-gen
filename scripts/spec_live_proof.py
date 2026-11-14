"""The controlled way to spend the Spec proof money (hard cap USD 5.00): one stage at a time, the spend checked before
and after each step, every finished run saved under docs/spec-agent/outputs/ at once.

    uv run python scripts/spec_live_proof.py plan                      # what each stage would cost, and the money left (no model)
    uv run python scripts/spec_live_proof.py run <input> [--repeat N]  # live runs of one input, each exported
    uv run python scripts/spec_live_proof.py proof [--runs N]          # the proof tools over the saved live packs
    add --offline to any stage to rehearse it with the gateway's mock mode (no key, no money, outputs labelled MOCK)

A live stage needs, set in the LAUNCH SHELL (never in .env): LLM_MODE=live MODEL_ROUTES=<route> SPEC_LIVE_BUDGET_USD=5.00
SPEC_TRIPWIRE_USD=4.50, and the key for the route's vendor in .env or the shell. This script never reads a key. It refuses
a stage whose worst case would take the booked spend past the tripwire, and stops after any run that did not finish.
`LLM_MODE=live` is the only live setting (CLAUDE.md rule 13); the cap is enforced by the service's own money wrapper too.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # hardening: allow - argv lists, no shell, fixed module names
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `evals` is not an installed package

#: input -> (profile, estimated worst-case USD for ONE run). Measured on the nine saved live runs of 2026-11-14 (Quick 0.04 to
#: 0.08, Standard 0.145, Thorough with Verify 0.54), then padded for the repairs a second pass can add; the synthetic
#: 60-screen app is sized from the Thorough run. Re-measured by `plan` once a run exists on this machine.
STAGES: dict[str, tuple[str, Decimal]] = {
    "quick-yoga-studio-bengaluru": ("compact", Decimal("0.12")),
    "standard-produce-marketplace-nashik": ("standard", Decimal("0.25")),
    "thorough-freight-fleet-india": ("deep", Decimal("0.90")),
    "synth:big": ("deep", Decimal("0.90")),
}
#: the proof tools on finished packs (PS-4, PS-6, PS-7, PS-10): call counts from docs/spec-agent/proof-tools.md
PROOF_ESTIMATE = Decimal("1.00")


def data_dir() -> Path:
    return Path(os.environ.get("SPEC_DATA_DIR") or ROOT / "agents" / "spec" / ".runtime")


def cap_usd() -> Decimal:
    return Decimal(os.environ.get("SPEC_LIVE_BUDGET_USD", "5.00"))


def tripwire_usd() -> Decimal:
    return Decimal(os.environ.get("SPEC_TRIPWIRE_USD", "4.50"))


def spent_usd() -> Decimal:
    """The highest reading of the spend record and the period ledgers (what scripts/spec_spend_check.py reports)."""
    record = 0
    rec = data_dir() / "spend-record.json"
    if rec.is_file():
        d = json.loads(rec.read_text(encoding="utf-8"))
        record = int(d.get("spent_micros", 0)) + sum(
            int(r["micros"]) for r in d.get("reservations", {}).values()
        )
    ledger = 0
    ledgers = ROOT / "data" / ".budget" / "spec"
    if ledgers.is_dir():
        for p in sorted(ledgers.glob("ledger-*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("event") == "commit":
                    c = ev.get("cost_micros")
                    if isinstance(c, int) and c > 0:
                        ledger += c
    return Decimal(max(record, ledger)) / Decimal(1_000_000)


def _fmt(x: Decimal) -> str:
    return f"USD {x.quantize(Decimal('0.01'))}"


def guard(stage: str, worst: Decimal, offline: bool) -> bool:
    """True when the stage may start. Offline stages cost nothing and always may."""
    if offline:
        return True
    spent = spent_usd()
    room = tripwire_usd() - spent
    print(
        f"{stage}: booked {_fmt(spent)}, tripwire {_fmt(tripwire_usd())}, cap {_fmt(cap_usd())}; worst case here {_fmt(worst)}"
    )
    if spent >= tripwire_usd():
        print(
            "REFUSED: the tripwire is already reached; only report-writing goes on.",
            file=sys.stderr,
        )
        return False
    if worst > room:
        print(
            f"REFUSED: the worst case ({_fmt(worst)}) is more than the room left under the tripwire ({_fmt(max(room, Decimal(0)))}). "
            "Drop a lower-value stage (order: Uber-Eats-class program, live PS-9, PS-10 extras, PS-5 repeats) or ask the owner for money.",
            file=sys.stderr,
        )
        return False
    return True


def cmd_plan(_: argparse.Namespace) -> int:
    spent = spent_usd()
    print(
        f"booked so far: {_fmt(spent)} of the {_fmt(cap_usd())} cap (tripwire {_fmt(tripwire_usd())})"
    )
    total = Decimal(0)
    for name, (profile, est) in STAGES.items():
        print(f"  {name:40s} {profile:9s} worst case about {_fmt(est)} per run")
        total += est
    print(
        f"  {'proof tools on the saved packs':40s} {'':9s} worst case about {_fmt(PROOF_ESTIMATE)}"
    )
    print(
        f"one of each stage plus the proof tools: about {_fmt(total + PROOF_ESTIMATE)}; room under the tripwire: {_fmt(tripwire_usd() - spent)}"
    )
    return 0


def _bench(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mde_spec_agent", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def run_once(name: str, offline: bool, label: str) -> tuple[str | None, str]:
    args = ["bench", "run", "--input", name] + (["--offline"] if offline else [])
    done = _bench(args)
    out = done.stdout + done.stderr
    rid = re.search(r"^run id: (\S+)", out, re.M)
    status = re.search(r"^status: (\S+)", out, re.M)
    run_id = rid.group(1) if rid else None
    state = status.group(1) if status else f"no status (exit {done.returncode})"
    if run_id:
        exp = _bench(["bench", "export", "--run", run_id, "--label", label])
        print(
            (exp.stdout or exp.stderr).strip().splitlines()[-3:]
            if exp.returncode
            else f"  exported {run_id}"
        )
    return run_id, state


def cmd_run(ns: argparse.Namespace) -> int:
    if ns.input not in STAGES:
        print(f"unknown input {ns.input!r}; choose one of: {', '.join(STAGES)}", file=sys.stderr)
        return 2
    _profile, est = STAGES[ns.input]
    label = ns.input.replace(":", "-")
    for i in range(ns.repeat):
        if not guard(f"{ns.input} (run {i + 1} of {ns.repeat})", est, ns.offline):
            return 3
        run_id, state = run_once(ns.input, ns.offline, label)
        now = spent_usd()
        print(f"  run {run_id}: {state}; booked now {_fmt(now)}")
        if state not in ("approved", "awaiting_g2"):
            print(
                "  the run did not finish: stopping here (nothing more is started).",
                file=sys.stderr,
            )
            return 4
    return 0


def _latest_exports(offline: bool) -> dict[str, Path]:
    """The newest exported run per size (quick, standard, thorough) under docs/spec-agent/outputs/<mode>-*/."""
    found: dict[str, Path] = {}
    prefix = "mock-" if offline else "live-"
    for folder in sorted((ROOT / "docs" / "spec-agent" / "outputs").glob(f"{prefix}*")):
        for run in sorted(folder.glob("run_*")):
            if not (run / "graph.json").is_file():
                continue
            profile = json.loads((run / "run.json").read_text(encoding="utf-8")).get("profile")
            compass = json.loads((run / "run.json").read_text(encoding="utf-8")).get(
                "compass_id", ""
            )
            if str(compass).startswith("synth:"):
                continue
            size = {"compact": "quick", "standard": "standard", "deep": "thorough"}.get(
                profile or ""
            )
            if size:
                found[size] = run  # sorted: the last one wins (newest)
    return found


def cmd_proof(ns: argparse.Namespace) -> int:
    from evals.spec import report
    from evals.spec.common import make_proof_runner
    from mde_core.settings import load_settings
    from mde_spec_core.graph import SpecGraph

    if not guard("proof tools", PROOF_ESTIMATE, ns.offline):
        return 3
    runs = _latest_exports(ns.offline)
    if not {"quick", "standard", "thorough"} <= set(runs):
        print(
            f"need an exported run of each size first (have: {sorted(runs)}); use the run stage.",
            file=sys.stderr,
        )
        return 2
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = (
        ROOT
        / "docs"
        / "spec-agent"
        / "outputs"
        / f"proof-{'mock' if ns.offline else 'live'}-{stamp}"
    )
    core = load_settings(llm_mode="mock") if ns.offline else load_settings()
    runner = make_proof_runner(out / "_work", core=core)
    graphs = {
        size: SpecGraph.model_validate_json((path / "graph.json").read_text(encoding="utf-8"))
        for size, path in runs.items()
    }
    rep = report.run_all(
        graphs,
        out,
        runner,
        planted_graph=graphs["thorough"],
        packs_live=not ns.offline,
        critic_vendor="same",
        canary_scope="changed",
        runs=ns.runs,
    )
    print(
        f"proof report written to {out.relative_to(ROOT)} (proof-report.txt is the plain-words one)"
    )
    print(f"booked now: {_fmt(spent_usd())}")
    del rep
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("input")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--offline", action="store_true")
    p = sub.add_parser("proof")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--offline", action="store_true")
    ns = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, "proof": cmd_proof}[ns.stage](ns)


if __name__ == "__main__":
    sys.exit(main())
