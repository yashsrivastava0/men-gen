"""Live eval batches L1 to L4 (brief 9.4): the entry point of ``just live-eval``. LEAD ONLY.

    uv run python -m evals.runners.live_batch                    # print the plan, run nothing
    uv run python -m evals.runners.live_batch --batch L1 --dry-run
    uv run python -m evals.runners.live_batch --batch L1         # runs live (needs LLM_MODE=live)

It REFUSES to run unless ALL of these hold, and says why in plain words when one does not:

* ``LLM_MODE=live`` is set in the OUTER environment, and
  ``MDE_FORBID_LIVE`` is not ``1`` (the pytest guard);
* the budget status read from the ledger allows it: the tripwire is not ``refuse_95`` (nor
  ``must_run_only_80`` unless ``--must-run``) and the headroom under the effective cap covers
  the batch allowance.

A batch runs its units one at a time and stops when the money it has spent reaches its allowance
(the ledger delta, not an estimate). Every live response is recorded as a cassette
(``tests/cassettes`` or ``CASSETTE_DIR``) so later runs replay for free. At the end it prints the
ledger delta, writes the raw result to ``evals/reports/live-<batch>.json`` (gitignored) and tells
you to run ``just budget`` and write the total into ``docs/implementation/PROGRESS.md``.

Agents other than the lead never run this module live: its refusal paths and its whole batch
logic are tested offline (``tests/e2e/test_eval_runners.py``, mock stacks, zero cost).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from evals.graders.injection import load_payloads, run_payload
from evals.graders.metrics import aggregate, collect_metrics
from evals.graders.sufficiency import ProbeReport, run_probe
from evals.harness.briefs import load_briefs
from evals.harness.catalog import ROOT
from evals.harness.driver import FlowDriver
from evals.harness.journeys import GUIDED, Journey, play_journey, reach_plan_gate
from evals.harness.persona import load_personas, play_persona
from evals.harness.stack import CallLog, OverlayProvider, Stack, StackOptions, build_stack
from evals.probes.sufficiency.spec import SPEC as PROBE_SPEC
from evals.runners._io import say, warn
from mde_contracts.calls import (
    BriefContext,
    C12Input,
    C12Output,
    OptionLite,
    OutlineScreen,
    SpecOutline,
)
from mde_contracts.gateway import BudgetStatus, CallContext, GatewayClient
from mde_core.money import micros_to_usd, usd_to_micros
from mde_core.settings import Settings, load_settings

RAW = ROOT / "evals" / "reports"
StackFactory = Callable[[Path, StackOptions], Stack]


# --------------------------------------------------------------------------- the plan (brief 9.4)


@dataclass(frozen=True)
class Batch:
    id: str
    title: str
    allowance_usd: str  # a ceiling for the batch, in USD
    plan: tuple[str, ...]

    @property
    def allowance_micros(self) -> int:
        return usd_to_micros(self.allowance_usd)


BATCHES: dict[str, Batch] = {
    "L1": Batch(
        "L1",
        "Discover",
        "1.20",
        (
            "10 personas, 2 runs each, scripted users, up to the Brief (Gate 1)",
            "records cassettes; reports cards per session, assumption rate, fallbacks used",
        ),
    ),
    "L2": Batch(
        "L2",
        "Research",
        "1.00",
        (
            "3 Light-depth research runs: commerce, booking services, internal workforce",
            "reports queries, facts, additions, sources, research status per run",
        ),
    ),
    "L3": Batch(
        "L3",
        "End to end",
        "2.00",
        (
            "5 full guided sessions, one per archetype, hi to spec_ready",
            "records the golden outputs, cost and latency per session, validator first-pass rate",
        ),
    ),
    "L4": Batch(
        "L4",
        "Safety and probes",
        "0.50",
        (
            "9 injection payloads through the live models (web-search results are scripted)",
            "the context sufficiency probe on the golden specs, and a critic check on a spec "
            "with a feature removed",
        ),
    ),
}


def plan_text() -> str:
    lines = [
        "Live eval batches (brief 9.4). L0 is `just live-smoke`. Nothing runs without --batch.",
        "",
    ]
    for b in BATCHES.values():
        lines.append(f"{b.id}  {b.title:<18} allowance USD {b.allowance_usd}")
        lines += [f"      - {step}" for step in b.plan]
    lines += [
        "",
        "Reserve: at least USD 1.00 stays unspent. Stop live work at 80% unless an item is must-run.",
        "Run:  --batch L1 --dry-run   (what would run)   |   --batch L1   (live; lead only)",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- the gate


def read_budget_status(settings: Settings) -> BudgetStatus:
    """The ledger's status without building any provider (read-only, no network, no key)."""
    from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
    from mde_llm_gateway.ledger import FileLedger
    from mde_llm_gateway.pricing import Pricing

    guard = BudgetGuard(
        BudgetConfig.load(cap_usd=settings.llm_budget_usd),
        Pricing.load(),
        FileLedger(settings.llm_budget_ledger),
        bills=True,
    )
    return guard.status()


def check_live_allowed(
    env: Mapping[str, str], status: BudgetStatus, batch: Batch, *, must_run: bool = False
) -> list[str]:
    """Why this batch must NOT run now (empty list = allowed). Never mentions a key value."""
    reasons: list[str] = []
    if env.get("LLM_MODE") != "live":
        reasons.append("LLM_MODE=live is not set in the outer environment")
    if env.get("MDE_FORBID_LIVE") == "1":
        reasons.append(
            "MDE_FORBID_LIVE=1 is set (the offline test guard): live calls are forbidden"
        )
    if status.tripwire == "refuse_95":
        reasons.append("the budget is at the 95% tripwire: every call would be refused")
    elif status.tripwire == "must_run_only_80" and not must_run:
        reasons.append(
            "the budget is at the 80% tripwire: only must-run items may run (--must-run)"
        )
    headroom = status.effective_cap_micros - status.spent_micros - status.reserved_micros
    if headroom < batch.allowance_micros:
        reasons.append(
            f"headroom USD {micros_to_usd(headroom):.4f} under the effective cap is less than the "
            f"{batch.id} allowance of USD {batch.allowance_usd}"
        )
    return reasons


# --------------------------------------------------------------------------- the context


@dataclass
class BatchContext:
    batch: Batch
    make_stack: StackFactory
    client: GatewayClient  # the product's gateway (C12 for the critic check)
    probe_client: GatewayClient  # a gateway that knows the probe call
    spent: Callable[[], int]  # money spent so far on the shared ledger, micro-dollars
    out_dir: Path
    quick: bool = False  # offline smoke of the batch logic: one unit of each kind
    runs_per_persona: int = 2
    start_spent: int = 0
    notes: list[str] = field(default_factory=lambda: [])

    def used(self) -> int:
        return self.spent() - self.start_spent

    def over_allowance(self) -> bool:
        return self.used() >= self.batch.allowance_micros


def _stopped(ctx: BatchContext, unit: str) -> bool:
    if ctx.over_allowance():
        ctx.notes.append(f"stopped before {unit}: the {ctx.batch.id} allowance is spent")
        return True
    return False


# --------------------------------------------------------------------------- the batches


async def run_l1(ctx: BatchContext) -> dict[str, Any]:
    personas = load_personas()[: 1 if ctx.quick else None]
    runs = 1 if ctx.quick else ctx.runs_per_persona
    rows: list[dict[str, Any]] = []
    for n in range(1, runs + 1):
        for persona in personas:
            if _stopped(ctx, f"{persona.id} run {n}"):
                break
            with tempfile.TemporaryDirectory() as tmp:
                stack = ctx.make_stack(Path(tmp), StackOptions())
                run = await play_persona(stack, persona, until="brief")
                metrics = await collect_metrics(f"{persona.id}#{n}", run.driver, stack)
            rows.append(
                {
                    "persona": persona.id,
                    "run": n,
                    "state": run.state,
                    "cards_asked": metrics.cards_asked,
                    "assumed_slots": metrics.assumed_slots,
                    "filled_slots": metrics.filled_slots,
                    "llm_calls": metrics.llm_calls,
                    "failed_calls": len(stack.calls.failed()),
                    "repaired_calls": len(stack.calls.repaired()),
                    "problems": run.problems,
                }
            )
    return {"units": rows}


async def _drive(
    ctx: BatchContext, journey: Journey, *, to: str
) -> tuple[FlowDriver, Stack, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        stack = ctx.make_stack(Path(tmp), StackOptions())
        driver = FlowDriver.for_user(stack)
        if to == "plan":
            await reach_plan_gate(driver, journey)
            return driver, stack, {}
        result = await play_journey(driver, journey)
        spec_text = result.spec.spec_text
        _save(ctx.out_dir / f"{journey.name}.APP_SPEC.txt", spec_text)
        return driver, stack, {"spec_bytes": len(spec_text.encode("utf-8"))}


def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


async def run_l2(ctx: BatchContext) -> dict[str, Any]:
    wanted = ("commerce", "booking_services", "internal_workforce")
    journeys = [j for j in GUIDED if j.archetype in wanted][: 1 if ctx.quick else None]
    rows: list[dict[str, Any]] = []
    for journey in journeys:
        if _stopped(ctx, journey.name):
            break
        driver, stack, _ = await _drive(ctx, journey, to="plan")
        note = await driver.research_note()
        counts = stack.calls.counts()
        rows.append(
            {
                "journey": journey.name,
                "research_status": note.note.status if note else None,
                "unavailable_reason": note.note.unavailable_reason if note else None,
                "queries": note.note.queries_run if note else 0,
                "facts": len(note.note.facts) if note else 0,
                "suggestions": len(note.note.suggestions) if note else 0,
                "research_calls": {
                    k: v for k, v in counts.items() if k.startswith(("S1", "C6", "C7", "C8"))
                },
                "search_calls": sum(r.usage.tool_calls for r in stack.calls.records),
            }
        )
    return {"units": rows}


async def run_l3(ctx: BatchContext) -> dict[str, Any]:
    journeys = list(GUIDED)[: 1 if ctx.quick else None]
    rows: list[dict[str, Any]] = []
    metrics_all = []
    for journey in journeys:
        if _stopped(ctx, journey.name):
            break
        driver, stack, extra = await _drive(ctx, journey, to="end")
        metrics = await collect_metrics(journey.name, driver, stack)
        metrics_all.append(metrics)
        rows.append({"journey": journey.name, **metrics.as_dict(), **extra})
    return {"units": rows, "aggregate": aggregate(metrics_all)}


async def critic_check(ctx: BatchContext) -> dict[str, Any]:
    """Does the critic notice a Brief feature that the outline lacks? (A spec with cart and
    checkout removed against a Brief that asks for them.)"""
    brief = next(b for b in load_briefs() if b.archetype == "commerce")
    from mde_spec_loader import parse_spec

    spec = parse_spec(brief.golden_path.read_bytes().decode("utf-8"))
    kept = [s for s in spec.screens if "cart" not in s.route and "checkout" not in s.route]
    outline = SpecOutline(
        screens=[
            OutlineScreen(
                id=s.id,
                route=s.route,
                recipe=s.recipe,
                flow=s.flow,
                purpose=s.purpose,
                source=s.source,
            )
            for s in kept
        ]
    )
    context = BriefContext(
        app_summary="An online shop for handmade candles with cart and checkout",
        archetype="commerce",
        features=[
            OptionLite(id="catalog_browse", label="Browse products"),
            OptionLite(id="cart_checkout", label="Cart and checkout"),
        ],
    )
    from mde_prompts.registry import load_spec

    result = await ctx.client.run(
        load_spec("C12.critic"),
        C12Input(brief=context, outline=outline),
        CallContext(
            request_id="req_critic_check", budget_scope="eval:critic", purpose="eval:critic"
        ),
    )
    out = cast("C12Output", result.output)
    return {
        "removed_screens": len(spec.screens) - len(kept),
        "verdict": out.verdict,
        "findings": [{"kind": f.kind, "severity": f.severity} for f in out.findings],
        "noticed": out.verdict == "fix" and bool(out.findings),
    }


async def run_l4(ctx: BatchContext) -> dict[str, Any]:
    payloads = load_payloads()
    if ctx.quick:
        payloads = [next(p for p in payloads if p.channel == "search_result")]
    injections: list[dict[str, object]] = []
    for payload in payloads:
        if _stopped(ctx, payload.id):
            break
        with tempfile.TemporaryDirectory() as tmp:
            outcome = await run_payload(payload, Path(tmp), factory=ctx.make_stack)
        injections.append(outcome.as_row())
    probes: list[dict[str, Any]] = []
    for brief in load_briefs()[: 1 if ctx.quick else None]:
        if _stopped(ctx, f"probe {brief.id}"):
            break
        text = brief.golden_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        report: ProbeReport = await run_probe(
            ctx.probe_client, text, limit=3 if ctx.quick else 6, scope=f"eval:{ctx.batch.id}"
        )
        probes.append({"brief": brief.id, **report.as_dict()})
    critic = None if _stopped(ctx, "the critic check") else await critic_check(ctx)
    return {
        "injections": injections,
        "injection_pass_rate": (
            sum(1 for r in injections if r["passed"]) / len(injections) if injections else None
        ),
        "probes": probes,
        "critic": critic,
    }


RUNNERS: dict[str, Callable[[BatchContext], Awaitable[dict[str, Any]]]] = {
    "L1": run_l1,
    "L2": run_l2,
    "L3": run_l3,
    "L4": run_l4,
}


async def execute_batch(ctx: BatchContext) -> dict[str, Any]:
    """Run one batch on whatever stack factory the context carries (live, or mock in the tests)."""
    ctx.start_spent = ctx.spent()
    result = await RUNNERS[ctx.batch.id](ctx)
    return {
        "batch": ctx.batch.id,
        "title": ctx.batch.title,
        "allowance_usd": ctx.batch.allowance_usd,
        "spent_micros": ctx.used(),
        "stopped_early": any("stopped before" in n for n in ctx.notes),
        "notes": ctx.notes,
        **result,
    }


# --------------------------------------------------------------------------- live wiring


def make_live_context(settings: Settings, batch: Batch, *, quick: bool = False) -> BatchContext:
    """A context over the REAL gateway (cassettes recorded, ledger shared). Lead only."""
    from mde_core.ids import new_id
    from mde_llm_gateway import StaticRegistry, create_gateway
    from mde_llm_gateway.budget import BudgetConfig
    from mde_llm_gateway.ledger import FileLedger
    from mde_llm_gateway.registry import AnySpec
    from mde_prompts.registry import PromptRegistry

    budget = BudgetConfig.load(cap_usd=settings.llm_budget_usd)
    config = replace(
        budget,
        scope_overrides_micros={
            **budget.scope_overrides_micros,
            f"eval:{batch.id}": batch.allowance_micros,
        },
    )
    gateway = create_gateway(
        settings, PromptRegistry(), record_cassettes=True, budget_config=config
    )
    base = gateway.provider
    probe_gateway = create_gateway(
        settings,
        StaticRegistry([cast("AnySpec", PROBE_SPEC)]),
        record_cassettes=True,
        budget_config=config,
    )
    from mde_llm_gateway import InProcessGatewayClient

    client = InProcessGatewayClient(gateway)

    def make_stack(path: Path, options: StackOptions) -> Stack:
        calls = CallLog()  # one call log per session: the metrics are per session
        gateway.sink = calls
        overlay = OverlayProvider(options.script, fallback=base)
        gateway.provider = overlay  # scripted entries (web-search payloads) over the live provider
        stack = build_stack(
            path, replace(options, gateway=gateway, calls=calls, script={}, ids=new_id)
        )
        stack.provider = overlay
        return stack

    ledger = FileLedger(settings.llm_budget_ledger)
    return BatchContext(
        batch=batch,
        make_stack=make_stack,
        client=client,
        probe_client=InProcessGatewayClient(probe_gateway),
        spent=lambda: ledger.peek().spent_micros,
        out_dir=RAW / f"live-{batch.id}",
        quick=quick,
    )


def _ledger_lines(settings: Settings) -> str:
    from mde_llm_gateway.ledger import FileLedger

    snap = FileLedger(settings.llm_budget_ledger).peek()
    scopes = ", ".join(
        f"{name}={micros_to_usd(m):.4f}" for name, m in sorted(snap.spent_by_scope.items())[-8:]
    )
    return f"ledger total USD {micros_to_usd(snap.spent_micros):.4f}; recent scopes: {scopes or 'none'}"


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    environ = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="Live eval batches (lead only)")
    parser.add_argument("--batch", choices=sorted(BATCHES), help="which batch to run")
    parser.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    parser.add_argument("--must-run", action="store_true", help="allow at the 80% tripwire")
    parser.add_argument("--runs", type=int, default=2, help="L1 runs per persona (default 2)")
    parser.add_argument(
        "--quick", action="store_true", help="one unit of each kind (a smoke of the batch logic)"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.batch is None:
        say(plan_text())
        return 0
    batch = BATCHES[args.batch]
    if args.dry_run:
        say(f"{batch.id} {batch.title}: allowance USD {batch.allowance_usd}")
        for step in batch.plan:
            say(f"  - {step}")
        say("dry run: nothing was called")
        return 0
    settings = load_settings()
    status = read_budget_status(settings)
    reasons = check_live_allowed(environ, status, batch, must_run=args.must_run)
    if reasons:
        warn(f"REFUSED: batch {batch.id} will not run live.")
        for reason in reasons:
            warn(f"  - {reason}")
        warn(
            "Only the lead (or the live-runner) runs live batches: see docs/runbooks/live-llm-budget.md"
        )
        return 2
    ctx = make_live_context(settings, batch, quick=args.quick)
    ctx.runs_per_persona = args.runs
    before = ctx.spent()
    result = asyncio.run(execute_batch(ctx))
    delta = ctx.spent() - before
    out = RAW / f"live-{batch.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(json.dumps(result, indent=2, default=str).encode("utf-8"))
    say(f"{batch.id} {batch.title}: done{' (stopped early)' if result['stopped_early'] else ''}")
    say(f"ledger delta USD {micros_to_usd(delta):.4f} of the USD {batch.allowance_usd} allowance")
    say(_ledger_lines(settings))
    say(f"raw result: {out} (gitignored); cassettes recorded for replay")
    say("next: `just budget`, then write the new total into docs/implementation/PROGRESS.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
