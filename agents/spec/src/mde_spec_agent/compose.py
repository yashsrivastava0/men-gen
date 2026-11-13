"""Composition root for Spec: builds the gateway, the spend record and guard, the call runner and a RunContext.

There is NO switch that selects a test double here: a run is either live (LLM_MODE=live through the gateway, within the
USD cap) or offline (the gateway's own mock mode, which runs each call's mock handler). Tests use `mde_spec_agent.testing`,
which this module never imports.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from mde_contracts.gateway import GatewayClient
from mde_core.clock import Clock, SystemClock
from mde_core.settings import Settings
from mde_llm_gateway import GatewayConfig, InProcessGatewayClient, StaticRegistry, create_gateway
from mde_llm_gateway.factory import route_fingerprint
from mde_spec_agent.calls.base import CallRunner, RunResult
from mde_spec_agent.calls.registry import all_calls
from mde_spec_agent.config import SpecSettings, guard_cap_usd, spec_budget_config
from mde_spec_agent.run.checkpoint import Versions
from mde_spec_agent.run.concurrency import Limiter, compute_k
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.spend import Prices, SpendGuard, SpendRecord
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.profiles import ConsumerProfile, ProfileSet, load_profiles

UNTRUSTED_CHARS = (
    120_000  # a module's slice and research pack are long; a silent cut would drop the tail
)


def gateway_config(*, offline: bool) -> GatewayConfig:
    conc = {"fast": 8, "standard": 8, "deep": 4}
    rpm = (
        {"fast": 60_000, "standard": 60_000, "deep": 60_000}
        if offline
        else {"fast": 600, "standard": 240, "deep": 120}
    )
    return GatewayConfig(concurrency=conc, rpm=rpm, max_untrusted_chars=UNTRUSTED_CHARS)


def build_gateway(
    core: Settings,
    settings: SpecSettings,
    clock: Clock,
    *,
    offline: bool,
    extra_specs: list[Any] | None = None,
    ledger_path: Path | None = None,
) -> InProcessGatewayClient:
    """The in-process gateway with Spec's own budget scopes. Offline = mock mode (each call's mock handler)."""
    if not offline and core.llm_mode != "live":
        raise RuntimeError("a live run needs LLM_MODE=live in the launch environment (CLAUDE.md rule 13)")
    mode_core = core.model_copy(update={"llm_mode": "mock" if offline else "live"})
    cap = guard_cap_usd(core.llm_budget_usd, settings.spec_live_budget_usd)
    mode_core = mode_core.model_copy(update={"llm_budget_usd": cap})
    if ledger_path is not None and not offline:
        # Spec's own ledger file for the budget period: the shared ledger (the archive of everything spent before)
        # is never touched, so the owner's USD 5.00 for Spec is really available (same arrangement as Research).
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        mode_core = mode_core.model_copy(update={"llm_budget_ledger": ledger_path})
    specs = [c.spec for c in all_calls()] + list(extra_specs or [])
    return InProcessGatewayClient(
        create_gateway(
            mode_core,
            StaticRegistry(specs),
            clock=clock,
            config=gateway_config(offline=offline),
            budget_config=spec_budget_config(cap, settings.spec_live_budget_usd),
        )
    )


def prices_of(profile: ConsumerProfile) -> Prices:
    """Worst-case prices from the writer profile (data). A profile without verified prices makes live runs refuse."""
    p = profile.price
    if not p.input_usd_per_million or not p.output_usd_per_million:
        raise ValueError(
            "the writer profile has no verified prices: a live run cannot reserve its worst case"
        )
    return Prices(
        p.input_usd_per_million,
        p.output_usd_per_million,
        p.long_prompt_threshold_tokens,
        p.long_prompt_multiplier,
    )


def period_ledger_path(record: SpendRecord) -> Path:
    """data/.budget/spec/ledger-<period>.jsonl in the MAIN checkout (what scripts/spec_spend_check.py also sums)."""
    from mde_core.paths import main_repo_root

    root = main_repo_root() or Path(__file__).resolve().parents[4]
    return root / "data" / ".budget" / "spec" / f"ledger-{record.snapshot()['period_id']}.jsonl"


def spend_record(settings: SpecSettings, data_dir: Path, clock: Clock | None = None) -> SpendRecord:
    return SpendRecord(
        data_dir / "spend-record.json",
        cap_micros=settings.cap_micros,
        tripwire_micros=settings.tripwire_micros,
        clock=clock,
    )


def versions_for(
    core: Settings,
    library_version: str,
    step: str,
    call_id: str = "",
    prompt_version: str = "1",
    schema_version: str = "1",
) -> Versions:
    return Versions(
        call_spec=call_id or step,
        prompt=prompt_version,
        schema=schema_version,
        library=library_version,
        route_fingerprint=route_fingerprint(core),
    )


def library_version() -> str:
    from mde_spec_core import libraries, vocab

    return f"{vocab.vocab_version()}.{libraries.libraries_version()}"


def build_context(
    *,
    store: RunStore,
    run_id: str,
    program_id: str,
    input_hash: str,
    graph: SpecGraph,
    core: Settings,
    settings: SpecSettings,
    record: SpendRecord,
    offline: bool,
    gateway: GatewayClient | None = None,
    profiles: ProfileSet | None = None,
    config: SpecConfig | None = None,
    consumer_id: str = "weak-default",
    on_call: Callable[[str, str, RunResult[Any]], None] | None = None,
    clock: Clock | None = None,
) -> RunContext:
    cfg = config or default_config()
    ps = profiles or load_profiles()
    writer = ps.get("weak-default")
    consumer = ps.get(consumer_id)
    clock = clock or SystemClock()
    gw = gateway or build_gateway(
        core, settings, clock, offline=offline, ledger_path=period_ledger_path(record)
    )
    guard = SpendGuard(record, prices_of(writer) if not offline else Prices("0", "0"))
    runner = CallRunner(gw, guard, run_id=run_id, on_call=on_call)
    k = compute_k(writer, avg_input_tokens=20_000, call_seconds=50, cfg=cfg)
    ctx = RunContext(
        run_id=run_id,
        program_id=program_id,
        input_hash=input_hash,
        store=store,
        graph=graph,
        config=cfg,
        writer=writer,
        consumer=consumer,
        versions=versions_for(core, library_version(), "run"),
        guard=guard,
        limiter=Limiter(k, cfg),
        runner=runner,
        offline=offline,
    )
    return ctx


__all__ = ["Decimal", "build_context", "build_gateway", "prices_of", "spend_record", "versions_for"]
